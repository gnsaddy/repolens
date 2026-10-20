"""Web links to commits, files and pull requests on GitHub / GitLab-style hosts."""

import base64
import re
from urllib.parse import quote, urlparse

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d+)?$")
_SSH_SCP = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)([\w./-]+?)(?:\.git)?/?$")


def web_base(url: str) -> str:
    """Any git remote URL -> 'https://host/owner/name' (never with credentials), or '' if not recognisable."""
    url = (url or "").strip()
    if not url:
        return ""
    host = path = ""
    if "://" in url:
        u = urlparse(url)
        if u.scheme not in ("https", "http", "ssh", "git"):
            return ""
        host, path = u.hostname or "", u.path
        if u.port and u.scheme in ("https", "http") and u.port not in (80, 443):
            host = f"{host}:{u.port}"  # self-hosted instance on a custom port
    else:
        m = _SSH_SCP.match(url)
        if not m:
            return ""
        host, path = m.group(1), "/" + m.group(2)
    path = re.sub(r"\.git/?$", "", path).strip("/")
    if not _HOST_RE.match(host) or path.count("/") < 1 or ".." in path:
        return ""
    return f"https://{host}/{quote(path, safe='/')}"


def has_embedded_credentials(url: str) -> bool:
    u = urlparse(url.strip())
    return u.scheme in ("https", "http") and bool(u.username or u.password)


def strip_credentials(url: str) -> str:
    u = urlparse(url)
    if u.scheme in ("https", "http") and (u.username or u.password):
        netloc = u.hostname or ""
        if u.port:
            netloc += f":{u.port}"
        return u._replace(netloc=netloc).geturl()
    return url


def _gitlab(base: str) -> bool:
    return "gitlab" in (urlparse(base).hostname or "").lower()


def commit_url(base: str, sha: str) -> str:
    if not base or not sha:
        return ""
    return f"{base}/-/commit/{sha}" if _gitlab(base) else f"{base}/commit/{sha}"


def file_url(base: str, sha: str, path: str, start: int | None = None, end: int | None = None) -> str:
    if not base or not path:
        return ""
    ref = sha if sha and re.fullmatch(r"[0-9a-f]{7,64}", sha) else "HEAD"
    mid = "/-/blob" if _gitlab(base) else "/blob"
    url = f"{base}{mid}/{ref}/{quote(path)}"
    if start:
        url += f"#L{start}" + (f"-L{end}" if end and end != start else "")
        if _gitlab(base):  # GitLab uses #L1-5
            url = url.replace("-L", "-")
    return url


def pr_url(base: str, number: int) -> str:
    if not base:
        return ""
    return f"{base}/-/merge_requests/{number}" if _gitlab(base) else f"{base}/pull/{number}"


def basic_auth_header(token: str) -> str:
    return "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
