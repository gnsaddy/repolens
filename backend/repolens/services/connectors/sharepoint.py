"""SharePoint / OneDrive document libraries through Microsoft Graph (app-only, read-only).

Needs an Entra ID app registration with the ``Sites.Read.All`` (or ``Sites.Selected``) application permission.
Reads .md, .txt, .html and .docx files. The Graph host is fixed; pagination links are only followed if they stay on it.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..confluence import storage_to_markdown
from . import Connector, ConnectorError, Ctx, FetchResult, Field, RemoteDoc, register, transports
from .base import epoch, front_matter, http_json, parse_ts, slug

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
MAX_DEPTH = 6
MAX_BYTES = 5_000_000
EXTS = (".md", ".txt", ".html", ".htm", ".docx")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_to_markdown(data: bytes) -> str:
    """Text, headings and tables from a .docx (a zip of XML). Anything else in the file is ignored."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 20_000_000:
                raise ConnectorError("A document is too large to read")
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        raise ConnectorError("A .docx file could not be opened") from None
    if re.search(r"<!DOCTYPE|<!ENTITY", xml, re.I):
        raise ConnectorError("A document contains unsupported XML")
    root = ET.fromstring(xml)
    lines: list[str] = []

    def para(p) -> str:
        style = p.find(f"{W}pPr/{W}pStyle")
        text = "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
        level = re.fullmatch(r"Heading(\d)", style.get(f"{W}val", "")) if style is not None else None
        if level and text:
            return f"{'#' * (int(level.group(1)) + 1)} {text}\n"
        if p.find(f"{W}pPr/{W}numPr") is not None and text:
            return f"- {text}"
        return text + "\n" if text else ""

    body = root.find(f"{W}body")
    for el in list(body if body is not None else []):
        if el.tag == f"{W}p":
            lines.append(para(el))
        elif el.tag == f"{W}tbl":
            rows = [
                [" ".join(para(p).strip() for p in tc.iter(f"{W}p")).strip() for tc in tr.iter(f"{W}tc")]
                for tr in el.iter(f"{W}tr")
            ]
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]))
                lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                lines.append("")
    return "\n".join(lines).strip() + "\n"


class SharePointSource(Connector):
    kind = "sharepoint"
    label = "SharePoint / OneDrive"
    description = (
        "Import .md, .txt, .html and .docx files from a site's document library using an Entra ID app (Sites.Read.All)."
    )
    fields = [
        Field("tenant", "Tenant id or domain", placeholder="contoso.onmicrosoft.com"),
        Field("client_id", "Application (client) id", placeholder="00000000-0000-0000-0000-000000000000"),
        Field(
            "site",
            "Site",
            placeholder="contoso.sharepoint.com:/sites/Engineering",
            help="hostname:/sites/Name, or just the hostname for the root site.",
        ),
        Field(
            "folder",
            "Folder (optional)",
            required=False,
            placeholder="Shared Documents/Runbooks",
            help="Path inside the default document library.",
        ),
        Field("token", "Client secret", type="password", secret=True),
    ]

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        tenant = str(config.get("tenant", "")).strip()
        client_id = str(config.get("client_id", "")).strip()
        site = str(config.get("site", "")).strip().removeprefix("https://").strip("/")
        folder = str(config.get("folder", "")).strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9.-]{3,100}", tenant):
            raise ConnectorError("Enter the tenant id or domain")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", client_id):
            raise ConnectorError("The client id should look like a GUID")
        m = re.fullmatch(r"([A-Za-z0-9.-]+\.sharepoint\.com)(:/(?:sites|teams)/[A-Za-z0-9._ -]+)?", site)
        if not m:
            raise ConnectorError("Site must look like contoso.sharepoint.com:/sites/Name")
        if ".." in folder or not re.fullmatch(r"[\w .()&,'/-]*", folder):
            raise ConnectorError("Folder contains characters that are not allowed")
        return {"tenant": tenant, "client_id": client_id, "site": site, "folder": folder}

    def describe(self, config: dict[str, Any]) -> str:
        return f"SharePoint · {config.get('site', '')}"

    def _http(self, ctx: Ctx) -> httpx.Client:
        return httpx.Client(timeout=40, transport=ctx.transport or transports.get("sharepoint"), follow_redirects=True)

    def _auth(self, ctx: Ctx, http: httpx.Client) -> dict[str, str]:
        d = http_json(
            http,
            "POST",
            f"{LOGIN}/{ctx.config['tenant']}/oauth2/v2.0/token",
            "Microsoft sign-in",
            data={
                "grant_type": "client_credentials",
                "client_id": ctx.config["client_id"],
                "client_secret": ctx.secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        if not d.get("access_token"):
            raise ConnectorError("Microsoft did not issue an access token")
        return {"Authorization": f"Bearer {d['access_token']}"}

    def _drive(self, http: httpx.Client, auth: dict, site: str) -> tuple[str, str]:
        s = http_json(http, "GET", f"{GRAPH}/sites/{site}", "SharePoint", headers=auth)
        drive = http_json(http, "GET", f"{GRAPH}/sites/{s['id']}/drive", "SharePoint", headers=auth)
        return drive["id"], s.get("displayName") or s.get("name", "")

    def check(self, ctx: Ctx) -> str:
        with self._http(ctx) as http:
            auth = self._auth(ctx, http)
            _, name = self._drive(http, auth, ctx.config["site"])
        return f"Connected to SharePoint site “{name}”"

    @staticmethod
    def _next(link: str | None) -> str | None:
        if not link:
            return None
        u = urlparse(link)
        if u.scheme != "https" or u.hostname != "graph.microsoft.com":
            raise ConnectorError("Microsoft returned an unexpected pagination link")
        return link

    def fetch(self, ctx: Ctx) -> FetchResult:
        docs: list[RemoteDoc] = []
        with self._http(ctx) as http:
            auth = self._auth(ctx, http)
            drive_id, _ = self._drive(http, auth, ctx.config["site"])
            folder = ctx.config.get("folder", "")
            first = f"{GRAPH}/drives/{drive_id}/root" + (f":/{quote(folder)}:" if folder else "") + "/children"
            stack = [(first, "sharepoint", 0)]
            while stack:
                url, base, depth = stack.pop(0)
                next_url: str | None = url
                while next_url:
                    d = http_json(
                        http,
                        "GET",
                        next_url,
                        "SharePoint",
                        headers=auth,
                        params={"$top": "200"} if next_url == url else None,
                    )
                    for it in d.get("value", []):
                        name = it.get("name", "")
                        if "folder" in it:
                            if depth < MAX_DEPTH:
                                stack.append(
                                    (
                                        f"{GRAPH}/drives/{drive_id}/items/{it['id']}/children",
                                        f"{base}/{slug(name, 'folder')}",
                                        depth + 1,
                                    )
                                )
                            continue
                        if not name.lower().endswith(EXTS) or int(it.get("size") or 0) > MAX_BYTES:
                            continue
                        r = http.get(f"{GRAPH}/drives/{drive_id}/items/{it['id']}/content", headers=auth)
                        if r.status_code >= 400:
                            continue
                        lower = name.lower()
                        if lower.endswith(".docx"):
                            try:
                                body = docx_to_markdown(r.content)
                            except ConnectorError:
                                continue  # one unreadable or hostile file must not fail the whole library
                        elif lower.endswith((".html", ".htm")):
                            body = storage_to_markdown(
                                re.sub(r"<(script|style)\b.*?</\1>", "", r.text, flags=re.S | re.I)
                            )
                        else:
                            if b"\x00" in r.content[:2048]:
                                continue
                            body = r.text
                        title = re.sub(r"\.(docx|md|txt|html?)$", "", name, flags=re.I)
                        updated = parse_ts(it.get("lastModifiedDateTime"))
                        link = it.get("webUrl", "")
                        md = front_matter(title, "sharepoint", link) + (
                            body if body.lstrip().startswith("#") else f"# {title}\n\n{body}"
                        )
                        docs.append(
                            RemoteDoc(
                                it["id"],
                                f"{base}/{slug(title, 'untitled')}.md",
                                title,
                                md,
                                link,
                                epoch(updated),
                                updated,
                            )
                        )
                        if len(docs) >= ctx.max_docs:
                            return FetchResult(docs, True)
                    next_url = self._next(d.get("@odata.nextLink"))
        return FetchResult(docs, False)


register(SharePointSource())
