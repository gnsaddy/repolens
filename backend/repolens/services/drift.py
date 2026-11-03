"""Documentation drift: when code changes, find the pages that describe it and flag them for review.

After a repository is re-indexed, the lines changed since the previous index are mapped to the functions, classes and
files they touch. Any documentation section the knowledge graph links to those symbols (a MENTIONS edge) belongs to a
page that may now be out of date. One open alert is kept per page and repository, accumulating the changed symbols.
An alert closes by itself when the page is edited afterwards.
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Change, CodeChunk, Document, DriftAlert, KGEdge, KGNode, Repository
from . import gitops
from .graph import TEST_PATH

log = logging.getLogger("repolens.drift")
MAX_SYMBOLS = 25
CODE_TYPES = ("function", "method", "class")

# Pages that record what was true when they were written: they are not wrong when the code moves on.
HISTORY = re.compile(
    r"(^|/)(changelogs?|changes|history|news|releases?|release[-_ ]?notes?|archive[sd]?)(/|\.[a-z]+$|$)"
    r"|changelog|release[-_ ]?notes?",
    re.I,
)


def is_history(path: str) -> bool:
    return bool(HISTORY.search(path))


def _specific_word(w: str) -> bool:
    w = w.strip("_")
    return (
        "_" in w
        or bool(re.search(r"[a-z][A-Z]", w))  # camelCase and PascalCase with more than one word
        or (w[:1].isupper() and len(w) >= 5)  # a class name
        or len(w) >= 12
    )


def specific(name: str, qualname: str = "") -> bool:
    """Whether a name is one a page could only mean the code by: `compute_total`, `RepoLinkStore`, `Orders.add_item`.
    A short plain word (`repo`, `ask`, `execute`) is as likely to be English as code, so a page that uses it is not
    evidence of a description of that function."""
    return any(_specific_word(p) for p in (qualname or name).split(".") if p)


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(a <= end and b >= start for a, b in ranges)


def changed_nodes(db: Session, repo: Repository, hunks: dict[str, list[tuple[int, int]]]) -> list[KGNode]:
    if not hunks:
        return []
    out = []
    for n in db.scalars(select(KGNode).where(KGNode.repo_id == repo.id, KGNode.path.in_(list(hunks)))):
        if n.type == "file":
            out.append(n)
        elif (
            n.type in CODE_TYPES
            and _overlaps(hunks[n.path], n.start_line, n.end_line or n.start_line)
            and specific(n.name, n.qualname)
            and not TEST_PATH.search(n.path)  # a fixture or helper in a test is not what a page describes
        ):
            out.append(n)
    return out


def _lines_changed(ranges: list[tuple[int, int]], start: int, end: int) -> int:
    return sum(max(0, min(b, end) - max(a, start) + 1) for a, b in ranges)


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
        if is_history(s.path):
            continue  # a changelog or release note records the past and is not out of date when code changes
        pages.setdefault((s.repo_id, s.path), {})[section_ids[s.id]] = by_id[section_ids[s.id]]
    flagged = 0
    now = datetime.now(UTC)
    for (doc_repo, doc_path), syms in pages.items():
        digest, title = _doc_hash(db, doc_repo, doc_path)
        items = [
            {
                "id": n.id,
                "name": n.name,
                "qualname": n.qualname or n.name,
                "path": n.path,
                "type": n.type,
                "lines": _lines_changed(hunks.get(n.path, []), n.start_line or 1, n.end_line or n.start_line or 1)
                if n.type != "file"
                else sum(b - a + 1 for a, b in hunks.get(n.path, [])),
            }
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


def prune(db: Session, org_id: str) -> int:
    """Drop open alerts that should never have been raised: pages that record history, and symbols too plain to be
    evidence (an alert left with none goes). Returns how many alerts went."""
    gone = 0
    for a in db.scalars(select(DriftAlert).where(DriftAlert.org_id == org_id, DriftAlert.status == "open")):
        keep = [s for s in a.symbols if s.get("type") == "file" or specific(s.get("name", ""), s.get("qualname", ""))]
        if is_history(a.doc_path) or not keep:
            db.delete(a)
            gone += 1
        elif len(keep) != len(a.symbols):
            a.symbols = keep
    return gone


def mention_lines(db: Session, doc_repo_id: str, doc_path: str, names: list[str]) -> dict[str, str]:
    """For each name, the first line of the page that mentions it (so a reader sees why the page was flagged)."""
    doc = db.scalar(select(Document.content).where(Document.repo_id == doc_repo_id, Document.path == doc_path))
    if doc is None:
        rows = db.execute(
            select(CodeChunk.start_line, CodeChunk.content)
            .where(CodeChunk.repo_id == doc_repo_id, CodeChunk.file_path == doc_path)
            .order_by(CodeChunk.start_line)
        ).all()
        doc = "\n".join(c for _, c in rows)
    lines = doc.splitlines()
    out: dict[str, str] = {}
    for name in names:
        pat = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
        hit = next((ln for ln in lines if pat.search(ln)), None)
        if hit:
            out[name] = hit.strip()[:220]
    return out


def recent_changes(db: Session, a: DriftAlert, paths: list[str]) -> dict[str, list[dict]]:
    """For each changed file, the commits between the alert's two revisions that touched it (subject and author)."""
    t0 = db.scalar(select(Change.committed_at).where(Change.repo_id == a.repo_id, Change.sha == a.from_sha))
    t1 = db.scalar(select(Change.committed_at).where(Change.repo_id == a.repo_id, Change.sha == a.to_sha))
    if t0 is None or t1 is None:
        return {}
    out: dict[str, list[dict]] = {p: [] for p in paths}
    for sha, message, files, author in db.execute(
        select(Change.sha, Change.message, Change.files, Change.author)
        .where(Change.repo_id == a.repo_id, Change.committed_at > t0, Change.committed_at <= t1)
        .order_by(Change.committed_at.desc())
    ):
        touched = {f.get("path") for f in files or [] if isinstance(f, dict)}
        for p in paths:
            if p in touched and len(out[p]) < 3:
                out[p].append(
                    {
                        "sha": sha[:8],
                        "subject": (message or "").splitlines()[0][:120] if message else "",
                        "author": author,
                    }
                )
    return {p: v for p, v in out.items() if v}


def refresh(db: Session, org_id: str) -> None:
    """Resolve alerts whose collection page has been edited or removed since the alert was raised."""
    changed = bool(prune(db, org_id))
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
