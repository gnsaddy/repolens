from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, session_factory
from ..deps import Principal, current_principal
from ..models import Change, CodeChunk, Document, IndexRun, RepoAccess, Repository, User
from ..schemas import AccessIn, RepoCreate, RepoOut, StorageCleanup
from ..security import redact_stored
from ..services import checkouts, gitops, retrieval
from ..services.access import scope_for
from ..services.indexer import clone_auth_for, index_repository
from ..services.links import has_embedded_credentials
from ..services.parsing import detect_language
from ..services.policy import authorize

router = APIRouter(prefix="/api", tags=["repositories"])
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="index")


def _get_repo(db: Session, p: Principal, repo_id: str) -> Repository:
    repo = db.get(Repository, repo_id)
    if repo is None or repo.org_id != p.org_id or not scope_for(db, p).can(repo.id):
        raise HTTPException(404, "Repository not found")  # never reveal tenants or restricted repositories
    return repo


def _run_index(repo_id: str) -> None:
    db = session_factory()()
    try:
        index_repository(db, repo_id)
    finally:
        db.close()


def schedule_index(repo_id: str) -> None:
    if get_settings().run_jobs_inline:
        _run_index(repo_id)
    else:
        _pool.submit(_run_index, repo_id)


def _validate_local(path: str) -> str:
    s = get_settings()
    resolved = Path(path).expanduser().resolve()
    if s.env != "dev":
        roots = [Path(r).resolve() for r in s.local_repo_roots]
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise HTTPException(400, "Local repositories must be under a configured root")
    if not resolved.is_dir() or not gitops.is_repo(str(resolved)):
        raise HTTPException(400, "Path is not a git repository")
    return str(resolved)


@router.get("/repositories", response_model=list[RepoOut])
def list_repos(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(Repository).where(Repository.org_id == p.org_id)
    ids = scope_for(db, p).ids_or_none()
    if ids is not None:
        q = q.where(Repository.id.in_(ids))
    return db.scalars(q.order_by(Repository.name)).all()


@router.get("/repositories/branches")
def list_branches(
    source: str = Query(pattern="^(local|git)$"),
    url: str = Query(min_length=1, max_length=1000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Branches available to index, listed without cloning (git ls-remote / local refs)."""
    authorize(db, p, "repo.create", "branches")
    try:
        if source == "local":
            names, default = gitops.list_local_branches(_validate_local(url))
        else:
            if not url.startswith(("https://", "ssh://", "git@")) or has_embedded_credentials(url):
                raise HTTPException(400, "Git URL must be https or ssh, without credentials")
            names, default = gitops.list_remote_branches(url, clone_auth_for(db, p.org_id, url))
    except gitops.GitError as e:
        raise HTTPException(400, f"Could not list branches: {e}") from None
    return {"branches": names, "default": default or (names[0] if names else "")}


@router.get("/repositories/storage")
def storage(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "repo.delete", "storage")
    items = checkouts.usage(db, p.org_id)
    return {
        "ttl_days": get_settings().checkout_ttl_days,
        "total_bytes": sum(i["bytes"] for i in items),
        "checkouts": items,
    }


@router.post("/repositories/storage/cleanup")
def storage_cleanup(body: StorageCleanup, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "repo.delete", "storage-cleanup")
    return checkouts.cleanup(db, p.org_id, body.older_than_days, body.dry_run)


@router.post("/repositories", response_model=RepoOut, status_code=201)
def create_repo(body: RepoCreate, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "repo.create", body.name)
    s = get_settings()
    branches = list(dict.fromkeys(b.strip() for b in body.branches if b.strip()))
    if len(branches) > s.max_branches:
        raise HTTPException(400, f"At most {s.max_branches} branches per repository")
    if bad := [b for b in branches if not gitops.valid_branch(b)]:
        raise HTTPException(400, f"Invalid branch name: {bad[0]}")
    names = [body.name] + [f"{body.name}@{b}" for b in branches[1:]]
    taken = set(db.scalars(select(Repository.name).where(Repository.org_id == p.org_id, Repository.name.in_(names))))
    if taken:
        raise HTTPException(409, f"Repository name already exists: {sorted(taken)[0]}")
    url = _validate_local(body.url) if body.source == "local" else body.url
    if body.source == "git" and not url.startswith(("https://", "ssh://", "git@")):
        raise HTTPException(400, "Git URL must be https or ssh")
    if body.source == "git" and has_embedded_credentials(url):
        raise HTTPException(400, "Do not put credentials in the URL; connect GitHub under Settings instead")
    rows = [
        Repository(org_id=p.org_id, name=n, source=body.source, url=url, branch=branches[i] if branches else "")
        for i, n in enumerate(names)
    ]
    db.add_all(rows)
    db.flush()
    if body.source == "git":  # all branches of one repository share a single clone
        shared = str(Path(s.repos_dir) / p.org_id / rows[0].id)
        for r in rows:
            r.local_path = shared
    db.commit()
    for r in rows:
        schedule_index(r.id)
    db.refresh(rows[0])
    return rows[0]


@router.get("/repositories/{repo_id}", response_model=RepoOut)
def get_repo(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _get_repo(db, p, repo_id)


@router.post("/repositories/{repo_id}/index", status_code=202)
def reindex(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    repo = _get_repo(db, p, repo_id)
    authorize(db, p, "repo.index", repo.name)
    if repo.status == "indexing":
        raise HTTPException(409, "Already indexing")
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)
    return {"status": "scheduled"}


@router.delete("/repositories/{repo_id}", status_code=204)
def delete_repo(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    repo = _get_repo(db, p, repo_id)
    authorize(db, p, "repo.delete", repo.name)
    from .knowledge import delete_repo_graph

    delete_repo_graph(db, repo.id)
    for model in (CodeChunk, Change, IndexRun):
        db.query(model).filter(model.repo_id == repo.id).delete()
    checkouts.remove_if_unshared(db, repo)
    db.delete(repo)
    db.commit()


@router.get("/repositories/{repo_id}/runs")
def runs(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _get_repo(db, p, repo_id)
    rows = db.scalars(
        select(IndexRun).where(IndexRun.repo_id == repo_id).order_by(IndexRun.started_at.desc()).limit(10)
    )
    return [
        {
            "id": r.id,
            "status": r.status,
            "commit_sha": r.commit_sha,
            "files": r.files_indexed,
            "chunks_written": r.chunks_written,
            "chunks_reused": r.chunks_reused,
            "secrets_redacted": r.secrets_redacted,
            "commits": r.commits_indexed,
            "error": r.error,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in rows
    ]


@router.get("/repositories/{repo_id}/tree")
def tree(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _get_repo(db, p, repo_id)
    rows = db.execute(
        select(CodeChunk.file_path, func.max(CodeChunk.end_line), func.count())
        .where(CodeChunk.repo_id == repo_id)
        .group_by(CodeChunk.file_path)
        .order_by(CodeChunk.file_path)
    )
    return [{"path": r[0], "lines": r[1], "chunks": r[2], "language": detect_language(r[0])} for r in rows]


@router.get("/repositories/{repo_id}/file")
def file_content(
    repo_id: str,
    path: str = Query(min_length=1),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    repo = _get_repo(db, p, repo_id)
    # only serve paths that are in the index -> no path traversal into arbitrary repo/host files
    known = db.scalar(select(CodeChunk.id).where(CodeChunk.repo_id == repo.id, CodeChunk.file_path == path).limit(1))
    if not known or not repo.indexed_sha:
        raise HTTPException(404, "File not found in index")
    if repo.source == "docs":
        doc = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
        if doc is None:
            raise HTTPException(404, "File not available")
        raw = doc.content.encode()
    else:
        checkout = repo.local_path or str(Path(repo.url).expanduser())
        try:
            raw = gitops.read_file(checkout, repo.indexed_sha, path)
        except gitops.GitError:
            if repo.source == "git" and not Path(checkout).exists():
                raise HTTPException(409, "The clone was removed to save disk space. Re-index to restore it.") from None
            raise HTTPException(404, "File not available") from None
    symbols = [
        {
            "name": c.symbol_name,
            "parent": c.parent_symbol,
            "type": c.chunk_type,
            "start": c.start_line,
            "end": c.end_line,
        }
        for c in retrieval.chunks_for_file(db, p.org_id, repo.id, path)
        if c.symbol_name
    ]
    return {
        "path": path,
        "sha": repo.indexed_sha,
        "language": detect_language(path),
        "content": redact_stored(raw.decode("utf-8", errors="replace"))[0],
        "symbols": symbols,
    }


@router.get("/repositories/{repo_id}/blame")
def blame(
    repo_id: str,
    path: str = Query(min_length=1),
    start: int = Query(1, ge=1),
    end: int = Query(1, ge=1),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Which commits last wrote lines start..end of a file at the indexed revision (the bug-introducing suspects)."""
    repo = _get_repo(db, p, repo_id)
    known = db.scalar(select(CodeChunk.id).where(CodeChunk.repo_id == repo.id, CodeChunk.file_path == path).limit(1))
    if not known or not repo.indexed_sha or repo.source == "docs":
        raise HTTPException(404, "File not found in index")
    checkout = repo.local_path or str(Path(repo.url).expanduser())
    counts = gitops.blame_range(checkout, repo.indexed_sha, path, start, max(start, end))
    rows = {
        c.sha: c
        for c in db.scalars(select(Change).where(Change.repo_id == repo.id, Change.sha.in_(list(counts) or ["-"])))
    }
    total = sum(counts.values()) or 1
    from ..services.links import commit_url

    return sorted(
        [
            {
                "sha": sha,
                "lines": n,
                "share": round(n / total, 3),
                "message": (rows[sha].message.splitlines() or [""])[0] if sha in rows else "",
                "author": rows[sha].author if sha in rows else "",
                "committed_at": rows[sha].committed_at if sha in rows else None,
                "url": commit_url(repo.web_url, sha),
            }
            for sha, n in counts.items()
        ],
        key=lambda x: -x["lines"],
    )


@router.get("/repositories/{repo_id}/commits")
def commits(
    repo_id: str,
    limit: int = Query(30, le=200),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    _get_repo(db, p, repo_id)
    rows = db.scalars(select(Change).where(Change.repo_id == repo_id).order_by(Change.committed_at.desc()).limit(limit))
    return [
        {
            "sha": c.sha,
            "author": c.author,
            "committed_at": c.committed_at,
            "message": c.message,
            "files": c.files[:20],
            "pr_number": c.pr_number,
        }
        for c in rows
    ]


@router.get("/search")
def search(
    q: str = Query(min_length=2, max_length=500),
    repo_id: str | None = None,
    k: int = Query(15, le=50),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    scope = scope_for(db, p)
    ids = scope.resolve([repo_id] if repo_id else None)
    names = {
        r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id)) if scope.can(r.id)
    }
    hits = retrieval.search_chunks(db, p.org_id, q, ids or None, k=k)
    return [
        {
            "repo_id": h.chunk.repo_id,
            "repo": names.get(h.chunk.repo_id, ""),
            "file": h.chunk.file_path,
            "symbol": h.chunk.symbol_name,
            "parent": h.chunk.parent_symbol,
            "type": h.chunk.chunk_type,
            "start_line": h.chunk.start_line,
            "end_line": h.chunk.end_line,
            "snippet": "\n".join(h.chunk.content.splitlines()[:12]),
            "score": round(h.score, 5),
            "vector_rank": h.vector_rank,
            "lexical_rank": h.lexical_rank,
            "symbol_match": h.symbol_match,
            "matched_terms": h.matched_terms,
        }
        for h in hits
    ]


@router.get("/repositories/{repo_id}/access")
def get_access(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "repo.access.manage", repo_id, read_only=True)
    repo = _get_repo(db, p, repo_id)
    users = db.scalars(
        select(User).join(RepoAccess, RepoAccess.user_id == User.id).where(RepoAccess.repo_id == repo.id)
    )
    return {"visibility": repo.visibility, "users": [{"id": u.id, "email": u.email, "name": u.name} for u in users]}


@router.put("/repositories/{repo_id}/access")
def set_access(repo_id: str, body: AccessIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Admin only. `restricted` hides the repository (and everything derived from it) from everyone not listed."""
    repo = _get_repo(db, p, repo_id)
    authorize(db, p, "repo.access.manage", repo.name, visibility=body.visibility, users=len(body.user_ids))
    valid = set(db.scalars(select(User.id).where(User.org_id == p.org_id, User.id.in_(body.user_ids or ["-"]))))
    if set(body.user_ids) - valid:
        raise HTTPException(400, "Unknown user in user_ids")
    repo.visibility = body.visibility
    db.query(RepoAccess).filter(RepoAccess.repo_id == repo.id).delete()
    if body.visibility == "restricted":
        db.add_all(RepoAccess(org_id=p.org_id, repo_id=repo.id, user_id=u, granted_by=p.user_id) for u in sorted(valid))
    db.commit()
    return get_access(repo_id, p, db)
