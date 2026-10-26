"""Keeping indexes fresh without anyone clicking: try a failed index again, and index what changed once a day."""

import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_factory
from ..models import Repository
from . import gitops
from .indexer import branch_ref, clone_auth_for

log = logging.getLogger("repolens.indexer")

# Errors another try cannot fix: the path, the address, the branch or the credentials are wrong.
PERMANENT = re.compile(
    r"not a git repository|does not appear to be a git|repository\b[^\n]{0,200}not found|authentication failed|"
    r"could not read username|"
    r"permission denied|access denied|no such file|not found in the repository|only https|invalid ",
    re.I,
)
STUCK_AFTER = timedelta(hours=3)  # an index that has said "indexing" this long was cut short
_lock = threading.Lock()
_tries: dict[str, int] = {}  # repository id -> retries made since its last success
_waiting: set[str] = set()  # repository ids with a retry already on its way


def retryable(error: str | None) -> bool:
    """A timeout or a dropped connection can pass by itself; a wrong path or password cannot."""
    return not (error and PERMANENT.search(error))


def after_run(
    repo_id: str,
    status: str,
    error: str | None,
    schedule: Callable[[str], None],
    delays: list[float],
    timer: Callable[..., threading.Timer] = threading.Timer,
) -> float | None:
    """Called when an index finishes. On a failure that can pass, schedules another try; returns the wait in seconds."""
    with _lock:
        if status == "completed":
            _tries.pop(repo_id, None)
            return None
        made = _tries.get(repo_id, 0)
        if status != "failed" or not retryable(error) or made >= len(delays) or repo_id in _waiting:
            if status == "failed" and not retryable(error):
                _tries.pop(repo_id, None)
            return None
        _tries[repo_id] = made + 1
        _waiting.add(repo_id)
    wait = delays[made]
    log.info(
        "indexing %s failed (%s); trying again in %.0fs (try %d of %d)", repo_id, error, wait, made + 1, len(delays)
    )
    t = timer(wait, _again, args=(repo_id, schedule))
    t.daemon = True
    t.start()
    return wait


def _again(repo_id: str, schedule: Callable[[str], None]) -> None:
    with _lock:
        _waiting.discard(repo_id)
    with session_factory()() as db:
        repo = db.get(Repository, repo_id)
        if repo is None or repo.status != "error":  # deleted, or someone already fixed it
            return
    schedule(repo_id)


def changed(db: Session, repo: Repository) -> bool:
    """Whether the repository needs indexing again: it never finished, its last index failed, or its code moved on.
    A repository that cannot be reached right now is left for the next pass."""
    if repo.source == "docs":
        return False
    if repo.status in ("indexing", "pending"):  # running now, unless the server stopped in the middle long ago
        stamp = repo.updated_at if repo.updated_at.tzinfo else repo.updated_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - stamp > STUCK_AFTER
    if repo.status == "error" or not repo.indexed_sha:
        return True
    try:
        if repo.source == "local":
            head = gitops.head_sha(str(Path(repo.url).expanduser().resolve()), branch_ref(repo))
        else:
            head = gitops.remote_head(repo.url, repo.branch, clone_auth_for(db, repo.org_id, repo.url))
    except gitops.GitError as e:
        log.info("could not check %s for changes: %s", repo.name, e)
        return False
    return bool(head) and head != repo.indexed_sha


def daily_pass(db: Session, schedule: Callable[[str], None]) -> list[str]:
    """Queue an index for every repository that changed. Returns their names."""
    queued = []
    for repo in db.scalars(select(Repository)):
        if changed(db, repo):
            schedule(repo.id)
            queued.append(repo.name)
    if queued:
        log.info("daily re-index queued for %s", ", ".join(queued))
    return queued


def hours() -> float:
    return get_settings().auto_reindex_hours
