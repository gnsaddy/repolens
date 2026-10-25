"""Google Docs / Drive folders via a service account (read-only). Share the folder with the service account's email.

A service account is used instead of a user's OAuth token because tokens for users expire after an hour, which would
break scheduled sync. The key is stored encrypted and only ever sent to Google's token endpoint.
"""

import json
import re
import time
from typing import Any

import httpx
import jwt

from ..confluence import storage_to_markdown
from . import Connector, ConnectorError, Ctx, FetchResult, Field, RemoteDoc, register, transports
from .base import epoch, front_matter, http_json, parse_ts, slug

TOKEN_URL = "https://oauth2.googleapis.com/token"  # fixed: never taken from the uploaded key file
DRIVE = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DOC = "application/vnd.google-apps.document"
FOLDER = "application/vnd.google-apps.folder"
TEXT_TYPES = {"text/markdown", "text/plain", "text/x-markdown"}
ID = re.compile(r"[A-Za-z0-9_-]{10,}")
MAX_DEPTH = 6
MAX_BYTES = 1_000_000


def folder_id_from(value: str) -> str:
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", value) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    ident = m.group(1) if m else value.strip()
    if not ID.fullmatch(ident):
        raise ConnectorError("Enter a Google Drive folder link or id")
    return ident


def parse_key(secret: str) -> dict:
    try:
        key = json.loads(secret)
    except ValueError:
        raise ConnectorError("Paste the whole service-account JSON key file") from None
    if (
        not isinstance(key, dict)
        or key.get("type") != "service_account"
        or not key.get("private_key")
        or not key.get("client_email")
    ):
        raise ConnectorError("That is not a service-account key (it needs client_email and private_key)")
    return key


class GoogleDocsSource(Connector):
    kind = "gdocs"
    label = "Google Docs"
    description = (
        "Import the Docs and Markdown files in a Drive folder. Share the folder (read-only) with a service account."
    )
    fields = [
        Field("folder", "Drive folder link or id", placeholder="https://drive.google.com/drive/folders/1AbC…"),
        Field(
            "token",
            "Service-account key (JSON)",
            type="textarea",
            secret=True,
            placeholder="Paste the contents of the key file you downloaded from Google Cloud",
            help="The folder must be shared with the key's client_email.",
        ),
    ]

    def validate(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"folder_id": folder_id_from(str(config.get("folder") or config.get("folder_id") or ""))}

    def describe(self, config: dict[str, Any]) -> str:
        return f"Google Drive · {config.get('folder_id', '')[:10]}…"

    def _token(self, ctx: Ctx, http: httpx.Client) -> str:
        key = parse_key(ctx.secret)
        now = int(time.time())
        try:
            assertion = jwt.encode(
                {"iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3000},
                key["private_key"],
                algorithm="RS256",
            )
        except (ValueError, TypeError, jwt.PyJWTError):
            raise ConnectorError("The private key in that file could not be read") from None
        d = http_json(
            http,
            "POST",
            TOKEN_URL,
            "Google sign-in",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        if not d.get("access_token"):
            raise ConnectorError("Google did not issue an access token")
        return d["access_token"]

    def _http(self, ctx: Ctx) -> httpx.Client:
        return httpx.Client(timeout=30, transport=ctx.transport or transports.get("gdocs"))

    def check(self, ctx: Ctx) -> str:
        with self._http(ctx) as http:
            token = self._token(ctx, http)
            d = http_json(
                http,
                "GET",
                f"{DRIVE}/files/{ctx.config['folder_id']}",
                "Google Drive",
                params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
        if d.get("mimeType") != FOLDER:
            raise ConnectorError("That link is not a folder")
        return f"Connected to Google Drive folder “{d.get('name', '')}”"

    def fetch(self, ctx: Ctx) -> FetchResult:
        docs: list[RemoteDoc] = []
        truncated = False
        with self._http(ctx) as http:
            auth = {"Authorization": f"Bearer {self._token(ctx, http)}"}
            stack = [(ctx.config["folder_id"], "gdocs", 0)]
            while stack:
                fid, base, depth = stack.pop(0)
                token = ""
                while True:
                    params = {
                        "q": f"'{fid}' in parents and trashed = false",
                        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size)",
                        "pageSize": "100",
                        "supportsAllDrives": "true",
                        "includeItemsFromAllDrives": "true",
                    }
                    if token:
                        params["pageToken"] = token
                    d = http_json(http, "GET", f"{DRIVE}/files", "Google Drive", params=params, headers=auth)
                    for f in d.get("files", []):
                        mime = f.get("mimeType", "")
                        if mime == FOLDER:
                            if depth < MAX_DEPTH:
                                stack.append((f["id"], f"{base}/{slug(f['name'], 'folder')}", depth + 1))
                            continue
                        if mime == DOC:
                            r = http.get(
                                f"{DRIVE}/files/{f['id']}/export", params={"mimeType": "text/html"}, headers=auth
                            )
                            if r.status_code >= 400:
                                continue  # one unreadable file must not fail the whole folder
                            body = storage_to_markdown(
                                re.sub(r"<(script|style)\b.*?</\1>", "", r.text, flags=re.S | re.I)
                            )
                        elif mime in TEXT_TYPES or f["name"].lower().endswith((".md", ".txt")):
                            if int(f.get("size") or 0) > MAX_BYTES:
                                continue
                            r = http.get(f"{DRIVE}/files/{f['id']}", params={"alt": "media"}, headers=auth)
                            if r.status_code >= 400 or b"\x00" in r.content[:2048]:
                                continue
                            body = r.text
                        else:
                            continue
                        title = re.sub(r"\.(md|txt)$", "", f["name"], flags=re.I)
                        updated = parse_ts(f.get("modifiedTime"))
                        url = f.get("webViewLink", "")
                        md = front_matter(title, "gdocs", url) + (
                            body if body.lstrip().startswith("#") else f"# {title}\n\n{body}"
                        )
                        docs.append(
                            RemoteDoc(
                                f["id"], f"{base}/{slug(title, 'untitled')}.md", title, md, url, epoch(updated), updated
                            )
                        )
                        if len(docs) >= ctx.max_docs:
                            return FetchResult(docs, True)
                    token = d.get("nextPageToken", "")
                    if not token:
                        break
        return FetchResult(docs, truncated)


register(GoogleDocsSource())
