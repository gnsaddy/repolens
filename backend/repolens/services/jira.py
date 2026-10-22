"""Jira REST client. Cloud: API token + email (REST v3, ADF). Data Center: PAT bearer (REST v2, plain text)."""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..config import get_settings


class JiraError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def validate_base_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise JiraError("Jira URL must be an http(s) URL")
    if get_settings().env != "dev":
        if u.scheme != "https":
            raise JiraError("Jira URL must use https")
        try:  # block SSRF to loopback / private / link-local literals
            ip = ipaddress.ip_address(u.hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise JiraError("Jira URL may not point at a private address")
        except ValueError:
            if u.hostname in ("localhost",) or u.hostname.endswith(".internal"):
                raise JiraError("Jira URL may not point at a private address") from None
    return url.rstrip("/")


def adf_to_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    inner = adf_to_text(node.get("content", []))
    if t in ("paragraph", "heading", "codeBlock", "listItem"):
        return inner + "\n"
    if t == "hardBreak":
        return "\n"
    return inner


_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _inline(text: str) -> list[dict]:
    """ADF text nodes; markdown links become real link marks (only http/https hrefs), emphasis markers are dropped."""
    nodes: list[dict] = []
    pos = 0
    for m in _LINK.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": re.sub(r"[*`]", "", text[pos : m.start()])})
        nodes.append(
            {
                "type": "text",
                "text": re.sub(r"[*`]", "", m.group(1)),
                "marks": [{"type": "link", "attrs": {"href": m.group(2)}}],
            }
        )
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": re.sub(r"[*`]", "", text[pos:])})
    return [n for n in nodes if n["text"]] or [{"type": "text", "text": " "}]


def markdown_to_adf(md: str) -> dict:
    content: list[dict] = []
    code: list[str] | None = None
    for line in md.splitlines():
        if line.strip().startswith("```"):
            if code is None:
                code = []
            else:
                content.append({"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code) or " "}]})
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if not line.strip():
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        inline = _inline(m.group(2) if m else line)
        if m:
            content.append({"type": "heading", "attrs": {"level": min(len(m.group(1)), 6)}, "content": inline})
        else:
            content.append({"type": "paragraph", "content": inline})
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph"}]}


@dataclass
class JiraIssue:
    key: str
    summary: str
    description: str
    status: str
    issue_type: str
    priority: str
    comments: list[str]
    labels: list[str]
    components: list[str]
    url: str

    def investigation_query(self) -> str:
        parts = [self.summary, self.description, *self.comments[-5:]]
        if self.components:
            parts.append("Components: " + ", ".join(self.components))
        return "\n\n".join(p for p in parts if p).strip()[:8000]


class JiraClient:
    def __init__(
        self,
        base_url: str,
        deployment: str,
        secret: str,
        email: str = "",
        transport: httpx.BaseTransport | None = None,
    ):
        self.base = validate_base_url(base_url)
        self.deployment = deployment
        self.api = "3" if deployment == "cloud" else "2"
        headers = {"Accept": "application/json"}
        auth = None
        if deployment == "cloud":
            auth = (email, secret)
        else:
            headers["Authorization"] = f"Bearer {secret}"
        self.http = httpx.Client(
            base_url=self.base,
            headers=headers,
            auth=auth,
            timeout=20,
            transport=transport,
            follow_redirects=False,
        )

    def _req(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, f"/rest/api/{self.api}{path}", **kw)
        except httpx.HTTPError as e:
            raise JiraError(f"Jira unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise JiraError(f"Jira returned {r.status_code}", r.status_code)
        return r.json() if r.content else {}

    def myself(self) -> dict:
        return self._req("GET", "/myself")

    def _issue_from(self, d: dict) -> JiraIssue:
        f = d.get("fields", {})
        desc = f.get("description")
        desc = adf_to_text(desc) if isinstance(desc, dict) else (desc or "")
        comments = []
        for c in (f.get("comment") or {}).get("comments", []):
            body = c.get("body")
            comments.append(adf_to_text(body).strip() if isinstance(body, dict) else (body or ""))
        return JiraIssue(
            key=d["key"],
            summary=f.get("summary", ""),
            description=desc.strip(),
            status=(f.get("status") or {}).get("name", ""),
            issue_type=(f.get("issuetype") or {}).get("name", ""),
            priority=(f.get("priority") or {}).get("name", ""),
            comments=comments,
            labels=f.get("labels", []),
            components=[c["name"] for c in f.get("components", [])],
            url=f"{self.base}/browse/{d['key']}",
        )

    def get_issue(self, key: str) -> JiraIssue:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key):
            raise JiraError("Invalid issue key")
        return self._issue_from(self._req("GET", f"/issue/{key}", params={"fields": "*all"}))

    def search(self, jql: str, limit: int = 25) -> list[JiraIssue]:
        fields = "summary,description,status,issuetype,priority,labels,components,comment"
        if self.deployment == "cloud":
            d = self._req("GET", "/search/jql", params={"jql": jql, "maxResults": limit, "fields": fields})
        else:
            d = self._req("GET", "/search", params={"jql": jql, "maxResults": limit, "fields": fields})
        return [self._issue_from(i) for i in d.get("issues", [])]

    def projects(self) -> list[dict]:
        if self.deployment == "cloud":
            d = self._req("GET", "/project/search", params={"maxResults": 100, "orderBy": "name"})
            raw = d.get("values", [])
        else:
            d = self._req("GET", "/project")
            raw = d if isinstance(d, list) else []
        return [{"key": p.get("key", ""), "name": p.get("name", "")} for p in raw if p.get("key")]

    def add_comment(self, key: str, markdown: str) -> str:
        body = markdown_to_adf(markdown) if self.deployment == "cloud" else markdown
        d = self._req("POST", f"/issue/{key}/comment", json={"body": body})
        return str(d.get("id", ""))
