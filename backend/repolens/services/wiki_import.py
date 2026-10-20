"""Wiki ingestion: Confluence spaces/pages (authenticated) and plain README/text URLs -> docs collections."""

import ipaddress
import logging
import posixpath
import re
import socket
from datetime import UTC, datetime
from urllib.parse import ParseResult, urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..config import get_settings
from ..db import session_factory
from ..models import ConfluenceConnection, Document, Repository, WikiSource
from ..security import decrypt_secret, redact_secrets, redact_stored
from .confluence import ConfluenceClient, ConfluenceError, parse_confluence_url, slugify, storage_to_markdown

log = logging.getLogger("repolens.wiki")
MAX_DEPTH = 25
DOC_EXT = (".md", ".mdx", ".rst", ".txt", ".adoc")

# Overridable in tests to inject a mock transport.
confluence_transport: httpx.BaseTransport | None = None
url_transport: httpx.BaseTransport | None = None


# ------------------------------------------------------------------------------------------------ Confluence
def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def collect_pages(client: ConfluenceClient, source: WikiSource) -> tuple[list[dict], bool, str]:
    """Pages to import (with bodies), whether the page cap truncated the set, and the root page id (tree mode)."""
    ref = parse_confluence_url(source.url)
    root: dict | None = None
    if ref.page_id:
        root = client.get_page(ref.page_id)
    elif ref.title and ref.space_key:
        root = client.find_page(ref.space_key, ref.title)
        if root is None:
            raise ConfluenceError(f"No page titled '{ref.title}' in space {ref.space_key}", 404)
    mode = source.mode
    if mode in ("page", "tree") and root is None:
        raise ConfluenceError("This URL points at a space; use mode 'space' or give a page URL")
    cap = source.max_pages
    if mode == "page":
        assert root is not None
        return [root], False, root["id"]
    pages: list[dict] = []
    truncated = False
    if mode == "tree":
        assert root is not None
        pages = [root]
        frontier = [(root["id"], 0)]
        while frontier and not truncated:
            pid, depth = frontier.pop(0)
            if depth >= MAX_DEPTH:
                continue
            for child in client.children(pid):
                if len(pages) >= cap:
                    truncated = True
                    break
                pages.append(child)
                frontier.append((child["id"], depth + 1))
        return pages, truncated, root["id"]
    space_key = ref.space_key or (root or {}).get("space", {}).get("key", "")
    if not space_key:
        raise ConfluenceError("Could not determine the space to import")
    for p in client.space_pages(space_key):
        if len(pages) >= cap:
            truncated = True
            break
        pages.append(p)
    return pages, truncated, ""


def build_documents(client: ConfluenceClient, pages: list[dict], root_id: str) -> list[dict]:
    """Stable paths, parent links and Markdown for each page."""
    by_id = {p["id"]: p for p in pages}
    used: dict[str, str] = {}
    out: list[dict] = []
    paths: dict[str, str] = {}
    for p in pages:
        space = slugify((p.get("space") or {}).get("key", "space"), "space")
        anc = [a for a in p.get("ancestors", []) if a.get("id") in by_id or not root_id]
        if root_id and root_id in [a["id"] for a in p.get("ancestors", [])]:
            anc = p["ancestors"][[a["id"] for a in p["ancestors"]].index(root_id) :]
            anc = [a for a in anc if a["id"] != p["id"]]
        elif p["id"] == root_id:
            anc = []
        segs = [space] + [slugify(a.get("title", "")) for a in anc]
        path = "/".join([*segs, slugify(p["title"]) + ".md"])
        if path in used and used[path] != p["id"]:
            path = path[:-3] + f"-{p['id']}.md"
        used[path] = p["id"]
        paths[p["id"]] = path
        out.append({"page": p, "path": path, "ancestor_ids": [a["id"] for a in anc]})
    for item in out:
        parents = [a for a in item["ancestor_ids"] if a in paths]
        item["parent_path"] = paths[parents[-1]] if parents else ""
        p = item["page"]
        title = p["title"].replace("\n", " ").replace('"', "'")
        url = client.web_url(p)
        ver = (p.get("version") or {}).get("number", 0)
        body = storage_to_markdown(((p.get("body") or {}).get("storage") or {}).get("value", ""))
        item["content"] = (
            f'---\ntitle: "{title}"\nsource: confluence\nurl: {url}\nspace: {(p.get("space") or {}).get("key", "")}\n'
            f"version: {ver}\n---\n\n# {p['title']}\n\n{body}\n"
        )
        item["url"], item["version"] = url, ver
        item["updated"] = _parse_dt(((p.get("history") or {}).get("lastUpdated") or {}).get("when")) or _parse_dt(
            (p.get("version") or {}).get("when")
        )
    return out


def sync_confluence(db: Session, source: WikiSource, client: ConfluenceClient) -> dict:
    pages, truncated, root_id = collect_pages(client, source)
    docs = build_documents(client, pages, root_id)
    existing = {
        d.source_id: d
        for d in db.scalars(
            select(Document).where(Document.repo_id == source.repo_id, Document.source_type == "confluence")
        )
    }
    stats = {
        "fetched": len(docs),
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "truncated": truncated,
        "secrets_redacted": 0,
    }
    now = datetime.now(UTC)
    fetched_ids = {d["page"]["id"] for d in docs}
    if not truncated:  # only a complete listing can prove a page is gone
        for sid, row in list(existing.items()):
            if sid not in fetched_ids:
                db.delete(row)
                stats["removed"] += 1
        db.flush()
        existing = {k: v for k, v in existing.items() if k in fetched_ids}
    taken = {d.path for d in existing.values()}
    for item in docs:
        pid = item["page"]["id"]
        content, red = redact_stored(item["content"])
        stats["secrets_redacted"] += red
        doc_row = existing.get(pid)
        path = item["path"]
        if doc_row is None:
            if db.scalar(select(Document.id).where(Document.repo_id == source.repo_id, Document.path == path)):
                path = path[:-3] + f"-{pid}.md"
            db.add(
                Document(
                    org_id=source.org_id,
                    repo_id=source.repo_id,
                    path=path,
                    title=item["page"]["title"][:500],
                    content=content,
                    content_hash=_hash(content),
                    created_by=source.created_by,
                    source_type="confluence",
                    source_url=item["url"],
                    source_id=pid,
                    source_version=item["version"],
                    parent_path=item["parent_path"],
                    source_updated_at=item["updated"],
                    last_synced_at=now,
                )
            )
            stats["created"] += 1
            taken.add(path)
            continue
        doc_row.last_synced_at = now
        same = doc_row.source_version == item["version"] and doc_row.content_hash == _hash(content)
        if same and doc_row.parent_path == item["parent_path"] and doc_row.path == path:
            flag_modified(doc_row, "updated_at")  # a no-op sync must not look like an edit
            stats["unchanged"] += 1
            continue
        if doc_row.path != path and path in taken:
            path = path[:-3] + f"-{pid}.md"
        doc_row.path, doc_row.title, doc_row.content, doc_row.content_hash = (
            path,
            item["page"]["title"][:500],
            content,
            _hash(content),
        )
        doc_row.source_url, doc_row.source_version, doc_row.parent_path = (
            item["url"],
            item["version"],
            item["parent_path"],
        )
        doc_row.source_updated_at = item["updated"]
        taken.add(path)
        stats["updated"] += 1
    db.commit()
    return stats


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def client_for_org(db: Session, org_id: str) -> ConfluenceClient:
    c = db.scalar(select(ConfluenceConnection).where(ConfluenceConnection.org_id == org_id))
    if c is None:
        raise ConfluenceError("Confluence is not connected for this organisation")
    return ConfluenceClient(
        c.base_url, c.deployment, decrypt_secret(c.secret_encrypted), c.email, transport=confluence_transport
    )


def run_source_sync(source_id: str) -> None:
    """Background job: fetch pages, store them, then index the collection (builds the knowledge graph)."""
    from .indexer import index_repository

    db = session_factory()()
    try:
        source = db.get(WikiSource, source_id)
        if source is None:
            return
        source.last_status, source.last_error = "running", None
        db.commit()
        try:
            stats = sync_confluence(db, source, client_for_org(db, source.org_id))
            repo = db.get(Repository, source.repo_id)
            if repo is not None:
                repo.status = "indexing"
                db.commit()
                index_repository(db, repo.id)
            source = db.get(WikiSource, source_id)
            assert source is not None
            source.last_status = "completed"
            source.pages_total = stats["fetched"]
            source.last_synced_at = datetime.now(UTC)
            source.last_error = "page limit reached; raise max_pages to import the rest" if stats["truncated"] else None
        except Exception as e:  # visible on the source, never crashes the worker
            db.rollback()
            log.exception("wiki sync failed for %s", source_id)
            source = db.get(WikiSource, source_id)
            assert source is not None
            source.last_status, source.last_error = "failed", redact_secrets(f"{type(e).__name__}: {e}")[:500]
        db.commit()
    finally:
        db.close()


def schedule_source_sync(source_id: str) -> None:
    from ..api.repos import _pool

    if get_settings().run_jobs_inline:
        run_source_sync(source_id)
    else:
        _pool.submit(run_source_sync, source_id)


# ------------------------------------------------------------------------------------------------ plain URLs
class FetchError(Exception):
    pass


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _check_url(url: str) -> ParseResult:
    u = urlparse(url)
    dev = get_settings().env == "dev"
    if u.scheme not in ("https", "http") or not u.hostname or (u.scheme == "http" and not dev):
        raise FetchError("Only https URLs can be imported")
    if u.username or u.password:
        raise FetchError("Do not put credentials in the URL")
    if not dev and not _public_host(u.hostname):
        raise FetchError("That address is not a public host")
    return u


def normalize_source_url(url: str) -> str:
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else url


def fetch_text_url(url: str, max_bytes: int = 1_000_000) -> tuple[str, str, str]:
    """(path, markdown/text, final_url) for a README / text / HTML page. https only, public hosts, no redirects off-site
    without re-checking, size-capped."""
    current = normalize_source_url(url.strip())
    with httpx.Client(timeout=20, transport=url_transport, follow_redirects=False) as hc:
        for _ in range(4):
            _check_url(current)
            try:
                with hc.stream("GET", current, headers={"Accept": "text/*, application/xhtml+xml"}) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        raise FetchError(f"The server answered {r.status_code}")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    buf = b""
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise FetchError("The page is larger than 1 MB")
                    break
            except httpx.HTTPError as e:
                raise FetchError(f"Could not fetch the URL ({type(e).__name__})") from e
        else:
            raise FetchError("Too many redirects")
    if b"\x00" in buf[:4096]:
        raise FetchError("That looks like a binary file")
    text = buf.decode("utf-8", errors="replace")
    name = posixpath.basename(urlparse(current).path) or "page"
    if ctype in ("text/html", "application/xhtml+xml"):
        found = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        title = found.group(1).strip() if found else ""
        body = re.sub(r"<(script|style|nav|header|footer)\b.*?</\1>", "", text, flags=re.S | re.I)
        md = storage_to_markdown(body)
        text = (f"# {title}\n\n" if title and not md.lstrip().startswith("# ") else "") + md
        name = (posixpath.splitext(name)[0] or "page") + ".md"
    elif not ctype.startswith("text/") and ctype not in ("application/markdown", "application/octet-stream", ""):
        raise FetchError(f"Unsupported content type: {ctype}")
    if not name.lower().endswith(DOC_EXT):
        name += ".txt"
    return name, text, current
