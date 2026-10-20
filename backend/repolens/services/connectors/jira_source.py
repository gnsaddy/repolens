"""Jira tickets as searchable history: "has this broken before?" answered from past incidents and their comments."""

from typing import Any

from sqlalchemy import select

from ...models import JiraConnection
from ...security import decrypt_secret
from ..jira import JiraClient, JiraError, adf_to_text
from . import Connector, ConnectorError, Ctx, FetchResult, Field, RemoteDoc, register, transports
from .base import epoch, front_matter, parse_ts, slug

FIELDS = "summary,description,status,issuetype,priority,labels,components,comment,updated,resolution,assignee,reporter,created,resolutiondate"


class JiraSource(Connector):
    kind = "jira"
    label = "Jira tickets"
    description = "Search past incidents and bugs, including their comments. Uses the Jira connection from Settings."
    needs_secret = False
    fields = [
        Field(
            "jql",
            "JQL query",
            placeholder="project = PAY AND issuetype = Bug AND updated >= -180d",
            help="Which issues to import. Keep it focused: bugs and incidents are the most useful.",
        ),
        Field("max_issues", "Max issues", type="number", required=False, placeholder="200"),
    ]

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        jql = str(config.get("jql", "")).strip()
        if len(jql) < 3 or len(jql) > 500:
            raise ConnectorError("Enter a JQL query (up to 500 characters)")
        try:
            n = int(config.get("max_issues") or 200)
        except (TypeError, ValueError):
            raise ConnectorError("Max issues must be a number") from None
        return {"jql": jql, "max_issues": max(1, min(n, 1000))}

    def describe(self, config: dict[str, Any]) -> str:
        return f"Jira · {config.get('jql', '')[:60]}"

    def _client(self, ctx: Ctx) -> JiraClient:
        c = ctx.db.scalar(select(JiraConnection).where(JiraConnection.org_id == ctx.org_id))
        if c is None:
            raise ConnectorError("Connect Jira under Settings first")
        try:
            return JiraClient(
                c.base_url,
                c.deployment,
                decrypt_secret(c.secret_encrypted),
                c.email,
                transport=ctx.transport or transports.get("jira"),
            )
        except JiraError as e:
            raise ConnectorError(str(e)) from None

    def check(self, ctx: Ctx) -> str:
        try:
            me = self._client(ctx).myself()
        except JiraError as e:
            raise ConnectorError(str(e)) from None
        return f"Connected to Jira as {me.get('displayName') or me.get('name') or 'the configured user'}"

    def fetch(self, ctx: Ctx) -> FetchResult:
        client = self._client(ctx)
        cap = int(ctx.config.get("max_issues", 200))
        raw: list[dict] = []
        try:
            token: str | None = None
            start = 0
            while len(raw) < cap:
                params: dict[str, Any] = {
                    "jql": ctx.config["jql"],
                    "maxResults": min(100, cap - len(raw)),
                    "fields": FIELDS,
                }
                if client.deployment == "cloud":
                    if token:
                        params["nextPageToken"] = token
                    page = client._req("GET", "/search/jql", params=params)
                    token = page.get("nextPageToken")
                else:
                    params["startAt"] = start
                    page = client._req("GET", "/search", params=params)
                    start += len(page.get("issues", []))
                raw.extend(page.get("issues", []))
                if (
                    not page.get("issues")
                    or (client.deployment == "cloud" and not token)
                    or (client.deployment != "cloud" and start >= page.get("total", 0))
                ):
                    break
        except JiraError as e:
            raise ConnectorError(str(e)) from None
        docs = [self._doc(client, i) for i in raw[:cap]]
        return FetchResult(docs, truncated=len(raw) >= cap)

    def _doc(self, client: JiraClient, d: dict) -> RemoteDoc:
        f = d.get("fields", {})
        key = d["key"]
        text = lambda v: adf_to_text(v).strip() if isinstance(v, dict) else (v or "").strip()  # noqa: E731
        summary = f.get("summary", "")
        name = lambda u: (u or {}).get("displayName") or (u or {}).get("name") or ""  # noqa: E731
        meta = [
            ("Type", (f.get("issuetype") or {}).get("name", "")),
            ("Status", (f.get("status") or {}).get("name", "")),
            ("Priority", (f.get("priority") or {}).get("name", "")),
            ("Resolution", (f.get("resolution") or {}).get("name", "")),
            ("Assignee", name(f.get("assignee"))),
            ("Reporter", name(f.get("reporter"))),
            ("Labels", ", ".join(f.get("labels", []))),
            ("Components", ", ".join(c.get("name", "") for c in f.get("components", []))),
        ]
        url = f"{client.base}/browse/{key}"
        body = [
            front_matter(f"{key}: {summary}", "jira", url, status=(f.get("status") or {}).get("name", "")),
            f"# {key}: {summary}",
            "",
        ]
        body += [f"- **{k}:** {v}" for k, v in meta if v]
        if text(f.get("description")):
            body += ["", "## Description", "", text(f.get("description"))]
        comments = (f.get("comment") or {}).get("comments", [])[-20:]
        if comments:
            body += ["", "## Comments", ""]
            for c in comments:
                body.append(
                    f"- **{name(c.get('author')) or 'someone'}** ({(c.get('created') or '')[:10]}): {text(c.get('body'))}".rstrip()
                )
        updated = parse_ts((f.get("updated") or "").replace("+0000", "+00:00"))
        project = key.rsplit("-", 1)[0]
        return RemoteDoc(
            key,
            f"jira/{slug(project, 'proj')}/{key}.md",
            f"{key}: {summary}",
            "\n".join(body) + "\n",
            url,
            epoch(updated),
            updated,
        )


register(JiraSource())
