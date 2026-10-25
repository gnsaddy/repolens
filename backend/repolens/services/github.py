"""Read-only GitHub REST client (pull requests, files, commits, comments)."""

import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..security import redact_stored


class GitHubError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def validate_api_base(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise GitHubError("GitHub API URL must be http(s)")
    if get_settings().env != "dev" and u.scheme != "https":
        raise GitHubError("GitHub API URL must use https")
    return url.rstrip("/")


def parse_github_repo(url: str) -> str | None:
    """'https://github.com/o/r.git' | 'git@github.com:o/r.git' | 'o/r' -> 'o/r'."""
    m = re.search(r"github[^/:]*[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC) if s else None


class GitHubClient:
    def __init__(
        self, token: str, api_base: str = "https://api.github.com", transport: httpx.BaseTransport | None = None
    ):
        self.base = validate_api_base(api_base)
        self.http = httpx.Client(
            base_url=self.base,
            timeout=30,
            transport=transport,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "repolens-ai",
            },
        )

    def _get(self, path: str, **params) -> list | dict:
        try:
            r = self.http.get(path, params=params)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {type(e).__name__}") from e
        if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("GitHub rate limit reached; try again later", r.status_code)
        if r.status_code >= 400:
            raise GitHubError(f"GitHub returned {r.status_code}", r.status_code)
        return r.json()

    # ---- writing: only used after a person approves a drafted fix ----
    def _send(self, method: str, path: str, body: dict) -> dict:
        try:
            r = self.http.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {type(e).__name__}") from e
        if r.status_code in (401, 403, 404):
            raise GitHubError(
                f"GitHub refused ({r.status_code}). The saved token needs write access to this repository "
                "(contents and pull requests).",
                r.status_code,
            )
        if r.status_code == 422:
            raise GitHubError(
                f"GitHub rejected the request: {(r.json().get('message') if r.content else '')[:160]}", 422
            )
        if r.status_code >= 400:
            raise GitHubError(f"GitHub returned {r.status_code}", r.status_code)
        out = r.json() if r.content else {}
        return out if isinstance(out, dict) else {}

    def default_branch(self, repo: str) -> str:
        d = self._get(f"/repos/{repo}")
        return str(d.get("default_branch", "main")) if isinstance(d, dict) else "main"

    def branch_head(self, repo: str, branch: str) -> str:
        d = self._get(f"/repos/{repo}/git/ref/heads/{branch}")
        return str(d["object"]["sha"]) if isinstance(d, dict) else ""

    def create_branch(self, repo: str, branch: str, sha: str) -> None:
        self._send("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})

    def file_at(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        """(text, blob sha) of a file on a branch."""
        import base64

        d = self._get(f"/repos/{repo}/contents/{path}", ref=ref)
        if not isinstance(d, dict) or d.get("encoding") != "base64":
            raise GitHubError("GitHub did not return that file's content")
        return base64.b64decode(d["content"]).decode("utf-8", errors="replace"), str(d["sha"])

    def put_file(self, repo: str, path: str, branch: str, text: str, message: str, blob_sha: str) -> None:
        import base64

        self._send(
            "PUT",
            f"/repos/{repo}/contents/{path}",
            {
                "message": message,
                "content": base64.b64encode(text.encode()).decode(),
                "branch": branch,
                "sha": blob_sha,
            },
        )

    def open_pull(self, repo: str, title: str, head: str, base: str, body: str, draft: bool = True) -> str:
        d = self._send(
            "POST", f"/repos/{repo}/pulls", {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        )
        return str(d.get("html_url", ""))

    def whoami(self) -> dict:
        d = self._get("/user")
        return d if isinstance(d, dict) else {}

    def list_pulls(self, repo: str, limit: int = 100) -> list[dict]:
        out: list[dict] = []
        page = 1
        while len(out) < limit:
            batch = self._get(
                f"/repos/{repo}/pulls", state="all", sort="updated", direction="desc", per_page=50, page=page
            )
            if not isinstance(batch, list) or not batch:
                break
            out += batch
            page += 1
        return out[:limit]

    def list_repos(self, limit: int = 500) -> tuple[list[dict], bool]:
        """Repositories the token can see (own, collaborator and organisation), most recently pushed first."""
        out: list[dict] = []
        page = 1
        while len(out) < limit:
            batch = self._get(
                "/user/repos",
                sort="pushed",
                direction="desc",
                per_page=100,
                page=page,
                affiliation="owner,collaborator,organization_member",
            )
            if not isinstance(batch, list) or not batch:
                return out, False
            out += batch
            if len(batch) < 100:
                return out, False
            page += 1
        return out[:limit], True

    def list_pulls_state(self, repo: str, state: str, limit: int = 50) -> list[dict]:
        d = self._get(f"/repos/{repo}/pulls", state=state, sort="updated", direction="desc", per_page=min(limit, 100))
        return d[:limit] if isinstance(d, list) else []

    def list_issues(self, repo: str, state: str, limit: int = 50) -> list[dict]:
        """Issues only: GitHub's issues endpoint also returns pull requests, which are listed separately."""
        out: list[dict] = []
        page = 1
        while len(out) < limit and page <= 4:
            d = self._get(
                f"/repos/{repo}/issues", state=state, sort="updated", direction="desc", per_page=100, page=page
            )
            if not isinstance(d, list) or not d:
                break
            out += [i for i in d if "pull_request" not in i]
            if len(d) < 100:
                break
            page += 1
        return out[:limit]

    def list_branches(self, repo: str) -> list[dict]:
        d = self._get(f"/repos/{repo}/branches", per_page=100)
        return d if isinstance(d, list) else []

    def get_pull(self, repo: str, number: int) -> dict:
        d = self._get(f"/repos/{repo}/pulls/{number}")
        return d if isinstance(d, dict) else {}

    def _list(self, path: str, limit: int) -> list[dict]:
        d = self._get(path, per_page=min(limit, 100))
        return d[:limit] if isinstance(d, list) else []

    def pull_files(self, repo: str, n: int) -> list[dict]:
        return self._list(f"/repos/{repo}/pulls/{n}/files", 300)

    def pull_commits(self, repo: str, n: int) -> list[dict]:
        return self._list(f"/repos/{repo}/pulls/{n}/commits", 250)

    def pull_comments(self, repo: str, n: int) -> list[dict]:
        review = self._list(f"/repos/{repo}/pulls/{n}/comments", 30)
        issue = self._list(f"/repos/{repo}/issues/{n}/comments", 30)
        return review + issue


JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")


def normalize_pull(p: dict, files: list[dict], commits: list[dict], comments: list[dict]) -> dict:
    state = "merged" if p.get("merged_at") else p.get("state", "open")
    text = f"{p.get('title', '')}\n{p.get('body') or ''}"
    return {
        "number": p["number"],
        "title": (p.get("title") or "")[:500],
        "body": redact_stored((p.get("body") or "")[:20000])[0],
        "state": state,
        "author": (p.get("user") or {}).get("login", ""),
        "base_ref": (p.get("base") or {}).get("ref", ""),
        "head_ref": (p.get("head") or {}).get("ref", ""),
        "merge_commit_sha": p.get("merge_commit_sha") or "",
        "url": p.get("html_url", ""),
        "labels": [lb.get("name", "") for lb in p.get("labels", [])],
        "files": [
            {"path": f["filename"], "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
            for f in files
        ],
        "commit_shas": [c["sha"] for c in commits if "sha" in c],
        "comments": [
            {
                "author": (c.get("user") or {}).get("login", ""),
                "body": redact_stored((c.get("body") or "")[:600])[0],
                "path": c.get("path", ""),
            }
            for c in comments
            if c.get("body")
        ][:40],
        "jira_keys": sorted(set(JIRA_KEY.findall(text + " " + p.get("head", {}).get("ref", "")))),
        "created_at": _dt(p.get("created_at")) or datetime.now(UTC),
        "merged_at": _dt(p.get("merged_at")),
    }


# ----------------------------------------------------------------------------------------------- OAuth device flow
def oauth_base(api_base: str) -> str:
    """https://api.github.com -> https://github.com ; GHES https://host/api/v3 -> https://host"""
    if api_base.rstrip("/") == "https://api.github.com":
        return "https://github.com"
    u = urlparse(api_base)
    return f"{u.scheme}://{u.netloc}"


def _post_form(url: str, data: dict, transport: httpx.BaseTransport | None) -> dict:
    try:
        with httpx.Client(timeout=20, transport=transport, follow_redirects=False) as hc:
            r = hc.post(url, data=data, headers={"Accept": "application/json", "User-Agent": "repolens-ai"})
    except httpx.HTTPError as e:
        raise GitHubError(f"GitHub unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise GitHubError(f"GitHub returned {r.status_code}", r.status_code)
    return r.json()


def device_start(client_id: str, scope: str, api_base: str, transport: httpx.BaseTransport | None = None) -> dict:
    d = _post_form(f"{oauth_base(api_base)}/login/device/code", {"client_id": client_id, "scope": scope}, transport)
    if "device_code" not in d:
        msg = d.get("error_description") or d.get("error") or "GitHub refused the request"
        raise GitHubError(f"{msg} (is Device Flow enabled for the OAuth App?)")
    return d


def device_poll(client_id: str, device_code: str, api_base: str, transport: httpx.BaseTransport | None = None) -> dict:
    return _post_form(
        f"{oauth_base(api_base)}/login/oauth/access_token",
        {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        transport,
    )
