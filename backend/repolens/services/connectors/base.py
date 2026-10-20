import hashlib
import re
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ...models import Document, WikiSource
from ...security import redact_stored
from . import ConnectorError, FetchResult


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def slug(text: str, fallback: str = "item", cap: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip().lower()).strip("-.")
    return (s or fallback)[:cap]


def front_matter(title: str, kind: str, url: str, **extra: str) -> str:
    t = title.replace("\n", " ").replace('"', "'")
    lines = [f'title: "{t}"', f"source: {kind}", f"url: {url}", *[f"{k}: {v}" for k, v in extra.items() if v]]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def http_json(client: httpx.Client, method: str, url: str, what: str, **kw) -> dict:
    """One API call with user-safe errors (status only, never the response body or any header)."""
    try:
        r = client.request(method, url, **kw)
    except httpx.HTTPError as e:
        raise ConnectorError(f"{what} is unreachable ({type(e).__name__})") from e
    if r.status_code in (401, 403):
        raise ConnectorError(f"{what} rejected the credentials or the token lacks permission ({r.status_code})")
    if r.status_code == 404:
        raise ConnectorError(f"{what}: not found (404). Check the id or that the token can see it.")
    if r.status_code == 429:
        raise ConnectorError(f"{what} is rate limiting requests; try again in a few minutes")
    if r.status_code >= 400:
        raise ConnectorError(f"{what} returned {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ConnectorError(f"{what} returned something that is not JSON") from e


def sync_documents(db: Session, source: WikiSource, result: FetchResult) -> dict:
    """Store fetched items in the source's collection. Unchanged items are left alone; items no longer returned by a
    complete listing are removed; secrets are redacted before storing."""
    kind = source.kind
    existing = {
        d.source_id: d
        for d in db.scalars(select(Document).where(Document.repo_id == source.repo_id, Document.source_type == kind))
    }
    stats = {
        "fetched": len(result.docs),
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "secrets_redacted": 0,
        "truncated": result.truncated,
    }
    fetched = {d.external_id for d in result.docs}
    if not result.truncated:
        for sid, old in list(existing.items()):
            if sid not in fetched:
                db.delete(old)
                stats["removed"] += 1
        db.flush()
        existing = {k: v for k, v in existing.items() if k in fetched}
    taken = {d.path for d in existing.values()}
    now = datetime.now(UTC)
    for item in result.docs:
        content, red = redact_stored(item.markdown)
        stats["secrets_redacted"] += red
        h = sha(content)
        row = existing.get(item.external_id)
        if row is None:
            path = item.path
            if path in taken or db.scalar(
                select(Document.id).where(Document.repo_id == source.repo_id, Document.path == path)
            ):
                stem, dot, ext = path.rpartition(".")
                path = (
                    f"{stem}-{slug(item.external_id, 'x', 20)}.{ext}"
                    if dot
                    else f"{path}-{slug(item.external_id, 'x', 20)}"
                )
            db.add(
                Document(
                    org_id=source.org_id,
                    repo_id=source.repo_id,
                    path=path,
                    title=item.title[:500],
                    content=content,
                    content_hash=h,
                    created_by=source.created_by,
                    source_type=kind,
                    source_url=item.url,
                    source_id=item.external_id,
                    source_version=item.version,
                    parent_path=item.parent_path,
                    source_updated_at=item.updated,
                    last_synced_at=now,
                )
            )
            taken.add(path)
            stats["created"] += 1
            continue
        row.last_synced_at = now
        if row.content_hash == h and row.source_version == item.version and row.parent_path == item.parent_path:
            flag_modified(row, "updated_at")
            stats["unchanged"] += 1
            continue
        row.title, row.content, row.content_hash = item.title[:500], content, h
        row.source_url, row.source_version, row.parent_path, row.source_updated_at = (
            item.url,
            item.version,
            item.parent_path,
            item.updated,
        )
        stats["updated"] += 1
    db.commit()
    return stats


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def epoch(dt: datetime | None) -> int:
    return int(dt.timestamp()) if dt else 0
