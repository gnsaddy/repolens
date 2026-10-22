"""Read-only Bitbucket client for Cloud (REST 2.0) and Server / Data Center (REST 1.0), with one normalised shape.

`owner` is the workspace (Cloud) or the project key (Server). Credentials are only sent as headers, never in URLs.
"""

import base64
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from .jira import JiraError, validate_base_url

CLOUD_API = "https://api.bitbucket.org/2.0"
CLOUD_HOST = "bitbucket.org"


class BitbucketError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def _ms(v: int | None) -> str | None:
    return datetime.fromtimestamp(v / 1000, UTC).isoformat() if v else None


class BitbucketClient:
    def __init__(
        self,
        deployment: str,
        token: str,
        username: str = "",
        base_url: str = "",
        transport: httpx.BaseTransport | None = None,
    ):
        self.deployment = deployment
        headers = {"Accept": "application/json", "User-Agent": "repolens-ai"}
        auth = None
        if deployment == "cloud":
            self.root = CLOUD_API
            if username:
                auth = (username, token)  # app password
            else:
                headers["Authorization"] = f"Bearer {token}"  # repository / workspace access token
        else:
            try:
                base = validate_base_url(base_url)
            except JiraError as e:
                raise BitbucketError(str(e)) from None
            self.root = f"{base}/rest/api/1.0"
            headers["Authorization"] = f"Bearer {token}"
        self.web_base = "" if deployment == "cloud" else base
        self.http = httpx.Client(timeout=30, headers=headers, auth=auth, transport=transport, follow_redirects=False)

    # -- low level ---------------------------------------------------------------------------------------------
    def _get(self, url: str, **params) -> dict:
        try:
            r = self.http.get(url, params=params or None)
        except httpx.HTTPError as e:
            raise BitbucketError(f"Bitbucket is unreachable ({type(e).__name__})") from e
        if r.status_code == 429:
            raise BitbucketError("Bitbucket rate limit reached; try again later", 429)
        if r.status_code in (401, 403):
            raise BitbucketError("Bitbucket rejected the credentials or they lack permission", r.status_code)
        if r.status_code == 404:
            raise BitbucketError("Not found on Bitbucket, or the credentials cannot see it", 404)
        if r.status_code >= 400:
            raise BitbucketError(f"Bitbucket returned {r.status_code}", r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise BitbucketError("Bitbucket returned something that is not JSON (wrong base URL?)") from e

    def _cloud_pages(self, url: str, limit: int, **params) -> tuple[list[dict], bool]:
        out: list[dict] = []
        nxt: str | None = url
        while nxt and len(out) < limit:
            d = self._get(nxt, **params) if nxt == url else self._get(self._safe_next(nxt))
            out += d.get("values", [])
            nxt = d.get("next")
        return out[:limit], bool(nxt) or len(out) > limit

    def _safe_next(self, link: str) -> str:
        if urlparse(link).hostname != urlparse(CLOUD_API).hostname:  # never follow a link off the API host
            raise BitbucketError("Bitbucket returned an unexpected pagination link")
        return link

    def _server_pages(self, path: str, limit: int, **params) -> tuple[list[dict], bool]:
        out: list[dict] = []
        start = 0
        while len(out) < limit:
            d = self._get(f"{self.root}{path}", limit=100, start=start, **params)
            out += d.get("values", [])
            if d.get("isLastPage", True):
                return out[:limit], False
            start = d.get("nextPageStart", start + 100)
        return out[:limit], True

    # -- API ---------------------------------------------------------------------------------------------------
    def whoami(self) -> str:
        if self.deployment == "cloud":
            d = self._get(f"{self.root}/user")
            return d.get("display_name") or d.get("username") or "Bitbucket user"
        self._get(f"{self.root}/projects", limit=1)  # the Server REST API has no "current user" without a username
        return "Bitbucket Server user"

    def list_repos(self, limit: int = 500) -> tuple[list[dict], bool]:
        """Normalised repositories: full_name = owner/slug, plus clone and web URLs."""
        out: list[dict] = []
        if self.deployment == "cloud":
            raw, more = self._cloud_pages(
                f"{self.root}/repositories", limit, role="member", sort="-updated_on", pagelen=100
            )
            for r in raw:
                clones = {c.get("name"): c.get("href", "") for c in (r.get("links") or {}).get("clone", [])}
                ws = (r.get("workspace") or {}).get("slug") or r.get("full_name", "/").split("/")[0]
                out.append(
                    {
                        "full_name": r.get("full_name", ""),
                        "name": r.get("slug") or r.get("name", ""),
                        "owner": ws,
                        "description": r.get("description") or "",
                        "private": bool(r.get("is_private")),
                        "fork": bool(r.get("parent")),
                        "archived": False,
                        "default_branch": (r.get("mainbranch") or {}).get("name", ""),
                        "language": r.get("language") or "",
                        "stars": 0,
                        "open_issues": 0,
                        "updated_at": r.get("updated_on"),
                        "html_url": ((r.get("links") or {}).get("html") or {}).get("href", ""),
                        "clone_url": _strip_user(clones.get("https", "")),
                    }
                )
            return out, more
        raw, more = self._server_pages("/repos", limit)
        for r in raw:
            proj = (r.get("project") or {}).get("key", "")
            clones = {c.get("name"): c.get("href", "") for c in (r.get("links") or {}).get("clone", [])}
            selfs = (r.get("links") or {}).get("self") or [{}]
            out.append(
                {
                    "full_name": f"{proj}/{r.get('slug', '')}",
                    "name": r.get("slug", ""),
                    "owner": proj,
                    "description": r.get("description") or "",
                    "private": not r.get("public", False),
                    "fork": bool(r.get("origin")),
                    "archived": bool(r.get("archived")),
                    "default_branch": "",
                    "language": "",
                    "stars": 0,
                    "open_issues": 0,
                    "updated_at": None,
                    "html_url": selfs[0].get("href", ""),
                    "clone_url": _strip_user(clones.get("http") or clones.get("https", "")),
                }
            )
        return out, more

    def pulls(self, owner: str, name: str, state: str, limit: int = 50) -> list[dict]:
        if self.deployment == "cloud":
            states = {
                "open": ["OPEN"],
                "closed": ["MERGED", "DECLINED", "SUPERSEDED"],
                "all": ["OPEN", "MERGED", "DECLINED"],
            }[state]
            raw, _ = self._cloud_pages(
                f"{self.root}/repositories/{owner}/{name}/pullrequests",
                limit,
                state=states,
                pagelen=50,
                sort="-updated_on",
            )
            return [
                {
                    "number": p["id"],
                    "title": (p.get("title") or "")[:300],
                    "state": {"OPEN": "open", "MERGED": "merged"}.get(p.get("state", ""), "closed"),
                    "draft": bool(p.get("draft")),
                    "author": (p.get("author") or {}).get("display_name", ""),
                    "head": ((p.get("source") or {}).get("branch") or {}).get("name", ""),
                    "base": ((p.get("destination") or {}).get("branch") or {}).get("name", ""),
                    "labels": [],
                    "url": ((p.get("links") or {}).get("html") or {}).get("href", ""),
                    "updated_at": p.get("updated_on"),
                }
                for p in raw
            ]
        st = {"open": "OPEN", "closed": "MERGED", "all": "ALL"}[state]
        raw, _ = self._server_pages(f"/projects/{owner}/repos/{name}/pull-requests", limit, state=st)
        return [
            {
                "number": p["id"],
                "title": (p.get("title") or "")[:300],
                "state": {"OPEN": "open", "MERGED": "merged"}.get(p.get("state", ""), "closed"),
                "draft": bool(p.get("draft")),
                "author": ((p.get("author") or {}).get("user") or {}).get("displayName", ""),
                "head": (p.get("fromRef") or {}).get("displayId", ""),
                "base": (p.get("toRef") or {}).get("displayId", ""),
                "labels": [],
                "url": (((p.get("links") or {}).get("self") or [{}])[0]).get("href", ""),
                "updated_at": _ms(p.get("updatedDate")),
            }
            for p in raw
        ]

    def branches(self, owner: str, name: str) -> list[dict]:
        if self.deployment == "cloud":
            raw, _ = self._cloud_pages(f"{self.root}/repositories/{owner}/{name}/refs/branches", 200, pagelen=100)
            return [{"name": b.get("name", ""), "protected": False} for b in raw]
        raw, _ = self._server_pages(f"/projects/{owner}/repos/{name}/branches", 200)
        return [{"name": b.get("displayId", ""), "protected": False, "default": bool(b.get("isDefault"))} for b in raw]


def _strip_user(url: str) -> str:
    """Bitbucket's clone links embed the user name (https://user@bitbucket.org/...). Never keep it."""
    u = urlparse(url)
    return u._replace(netloc=u.hostname + (f":{u.port}" if u.port else "")).geturl() if u.hostname else url


def clone_header(deployment: str, token: str, username: str = "") -> str:
    """git http.extraHeader value: Basic for Cloud (app password, or x-token-auth for access tokens), Bearer for Server."""
    if deployment == "server":
        return f"Authorization: Bearer {token}"
    user = username or "x-token-auth"
    return "Authorization: Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()
