"""Documentation drift: when code changes, find the pages that describe it and flag them for review.

After a repository is re-indexed, the lines changed since the previous index are mapped to the functions, classes and
files they touch. Any documentation section the knowledge graph links to those symbols (a MENTIONS edge) belongs to a
page that may now be out of date. One open alert is kept per page and repository, accumulating the changed symbols.
An alert closes by itself when the page is edited afterwards.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, DriftAlert, KGEdge, KGNode, Repository
from . import gitops

log = logging.getLogger("repolens.drift")
MAX_SYMBOLS = 25
CODE_TYPES = ("function", "method", "class")


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(a <= end and b >= start for a, b in ranges)


def changed_nodes(db: Session, repo: Repository, hunks: dict[str, list[tuple[int, int]]]) -> list[KGNode]:
    if not hunks:
        return []
    out = []
    for n in db.scalars(select(KGNode).where(KGNode.repo_id == repo.id, KGNode.path.in_(list(hunks)))):
        if n.type == "file":
            out.append(n)
        elif n.type in CODE_TYPES and _overlaps(hunks[n.path], n.start_line, n.end_line or n.start_line):
            out.append(n)
    return out


def _doc_hash(db: Session, repo_id: str, path: str) -> tuple[str, str]:
    d = db.scalar(select(Document).where(Document.repo_id == repo_id, Document.path == path))
    return (d.content_hash, d.title) if d else ("", "")


def detect(db: Session, repo: Repository, checkout: str, prev_sha: str, new_sha: str) -> int:
    """Create or update drift alerts for the change prev_sha..new_sha. Returns the number of pages flagged."""
    if not prev_sha or prev_sha == new_sha:
        return 0
    try:
        hunks = gitops.range_hunks(checkout, prev_sha, new_sha)
    except gitops.GitError:
        return 0  # the previous revision is no longer reachable (history rewritten): nothing to compare
    _close_edited_docs_in_repo(db, repo, set(hunks))
    nodes = changed_nodes(db, repo, hunks)
    if not nodes:
        return 0
    by_id = {n.id: n for n in nodes}
    edges = db.scalars(
        select(KGEdge).where(KGEdge.org_id == repo.org_id, KGEdge.rel == "MENTIONS", KGEdge.dst_id.in_(list(by_id)))
    )
    section_ids = {e.src_id: e.dst_id for e in edges}
    if not section_ids:
        return 0
    pages: dict[tuple[str, str], dict[str, KGNode]] = {}
    for s in db.scalars(select(KGNode).where(KGNode.id.in_(list(section_ids)), KGNode.type == "section")):
        if s.repo_id == repo.id and s.path in hunks:
            continue  # the doc changed in the same change set; it was updated together with the code
        pages.setdefault((s.repo_id, s.path), {})[section_ids[s.id]] = by_id[section_ids[s.id]]
    flagged = 0
    now = datetime.now(UTC)
    for (doc_repo, doc_path), syms in pages.items():
        digest, title = _doc_hash(db, doc_repo, doc_path)
        items = [
            {"id": n.id, "name": n.name, "qualname": n.qualname or n.name, "path": n.path, "type": n.type}
            for n in syms.values()
        ]
        alert = db.scalar(
            select(DriftAlert).where(
                DriftAlert.repo_id == repo.id,
                DriftAlert.doc_repo_id == doc_repo,
                DriftAlert.doc_path == doc_path,
                DriftAlert.status == "open",
            )
        )
        if alert is None:
            db.add(
                DriftAlert(
                    org_id=repo.org_id,
                    repo_id=repo.id,
                    doc_repo_id=doc_repo,
                    doc_path=doc_path,
                    doc_title=title or doc_path,
                    symbols=items[:MAX_SYMBOLS],
                    from_sha=prev_sha,
                    to_sha=new_sha,
                    doc_hash=digest,
                )
            )
        else:
            known = {s["id"] for s in alert.symbols}
            alert.symbols = [*alert.symbols, *[i for i in items if i["id"] not in known]][:MAX_SYMBOLS]
            alert.to_sha, alert.updated_at = new_sha, now
        flagged += 1
    db.flush()
    return flagged


def _close_edited_docs_in_repo(db: Session, repo: Repository, changed_files: set[str]) -> None:
    """Docs that live in the code repository itself: editing the file resolves its open alerts."""
    for a in db.scalars(select(DriftAlert).where(DriftAlert.doc_repo_id == repo.id, DriftAlert.status == "open")):
        if a.doc_path in changed_files:
            a.status, a.resolved_at = "resolved", datetime.now(UTC)


def refresh(db: Session, org_id: str) -> None:
    """Resolve alerts whose collection page has been edited or removed since the alert was raised."""
    changed = False
    for a in db.scalars(select(DriftAlert).where(DriftAlert.org_id == org_id, DriftAlert.status == "open")):
        doc_repo = db.get(Repository, a.doc_repo_id)
        if doc_repo is None or doc_repo.source != "docs":
            continue
        digest, _ = _doc_hash(db, a.doc_repo_id, a.doc_path)
        if digest != a.doc_hash:
            a.status, a.resolved_at = "resolved", datetime.now(UTC)
            changed = True
    if changed:
        db.commit()
