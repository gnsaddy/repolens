"""Disk usage and cleanup of cloned repositories. Only clones under `repos_dir` are ever touched."""

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Repository
from .indexer import checkout_lock

log = logging.getLogger("repolens.checkouts")


def _managed(path: str) -> Path | None:
    """The clone directory, only if it is inside the configured repos_dir (never a user's local repository)."""
    if not path:
        return None
    root = Path(get_settings().repos_dir).resolve()
    p = Path(path).resolve()
    return p if root in p.parents else None


def dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _groups(db: Session, org_id: str) -> dict[str, list[Repository]]:
    out: dict[str, list[Repository]] = {}
    for r in db.scalars(select(Repository).where(Repository.org_id == org_id, Repository.source == "git")):
        if _managed(r.local_path):
            out.setdefault(str(Path(r.local_path).resolve()), []).append(r)
    return out


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


def usage(db: Session, org_id: str) -> list[dict]:
    rows = []
    for path, repos in _groups(db, org_id).items():
        p = Path(path)
        used = max((x for x in (_aware(r.last_used_at) for r in repos) if x), default=None)
        rows.append(
            {
                "path": path,
                "repos": sorted(r.name for r in repos),
                "exists": p.exists(),
                "bytes": dir_size(p) if p.exists() else 0,
                "last_used_at": used,
                "indexing": any(r.status == "indexing" for r in repos),
                "partial": (p / ".git").exists() and _is_partial(str(p)),
            }
        )
    return sorted(rows, key=lambda r: -r["bytes"])


def _is_partial(path: str) -> bool:
    from . import gitops

    return gitops.is_partial(path)


def cleanup(db: Session, org_id: str, older_than_days: int, dry_run: bool = False) -> dict:
    """Delete clones not used for `older_than_days` days. Indexes stay; the clone is re-created on the next index."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    removed, freed = [], 0
    for path, repos in _groups(db, org_id).items():
        p = Path(path)
        used = max((x for x in (_aware(r.last_used_at) for r in repos) if x), default=None)
        if not p.exists() or any(r.status == "indexing" for r in repos) or (used and used > cutoff):
            continue
        size = dir_size(p)
        removed.append({"path": path, "repos": sorted(r.name for r in repos), "bytes": size})
        freed += size
        if not dry_run:
            with checkout_lock(path):
                shutil.rmtree(p, ignore_errors=True)
    if removed and not dry_run:
        log.info("removed %d stale checkout(s), freed %d bytes", len(removed), freed)
    return {"removed": removed, "freed_bytes": freed, "dry_run": dry_run}


def remove_if_unshared(db: Session, repo: Repository) -> None:
    """On repository delete: drop its clone unless a sibling branch still uses it."""
    p = _managed(repo.local_path)
    if p is None:
        return
    others = db.scalar(select(Repository.id).where(Repository.local_path == repo.local_path, Repository.id != repo.id))
    if others is None and p.exists():
        with checkout_lock(str(p)):
            shutil.rmtree(p, ignore_errors=True)
