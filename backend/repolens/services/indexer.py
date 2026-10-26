"""Ingestion: git repositories or uploaded doc collections -> symbol/section chunks -> embeddings -> graph.

Incremental by content hash: unchanged chunks keep their row and embedding.
"""

import hashlib
import logging
import threading
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Change, CodeChunk, Document, IndexRun, Repository
from ..security import redact_stored
from . import embedding_state, gitops, routes, usage
from .embeddings import Embedder
from .graph import build_repo_graph
from .links import web_base
from .parsing import detect_language, parse_file, should_index

log = logging.getLogger("repolens.indexer")


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def checkout_lock(path: str) -> threading.Lock:
    """Branches of one repository share a clone; serialise clone/fetch on it."""
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


def clone_auth_for(db: Session | None, org_id: str, url: str) -> str | None:
    """Use the organisation's connected GitHub token for https URLs of that host; never embedded in the URL."""
    if db is None or not url.startswith("https://"):
        return None
    from ..models import GithubConnection
    from .github import oauth_base
    from .links import basic_auth_header

    conn = db.scalar(select(GithubConnection).where(GithubConnection.org_id == org_id))
    if (
        conn is not None
        and conn.token_encrypted
        and urlparse(oauth_base(conn.api_base)).hostname == urlparse(url).hostname
    ):
        from ..security import decrypt_secret

        return basic_auth_header(decrypt_secret(conn.token_encrypted))
    return _bitbucket_clone_auth(db, org_id, url)  # never send a token to a host it was not issued for


def _bitbucket_clone_auth(db: Session, org_id: str, url: str) -> str | None:
    from ..models import BitbucketConnection
    from ..security import decrypt_secret
    from .bitbucket import CLOUD_HOST, clone_header

    b = db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == org_id))
    if b is None or not b.token_encrypted:
        return None
    host = urlparse(url).hostname
    allowed = CLOUD_HOST if b.deployment == "cloud" else urlparse(b.base_url).hostname
    return (
        clone_header(b.deployment, decrypt_secret(b.token_encrypted), b.username) if host and host == allowed else None
    )


def _clone_auth(db: Session | None, repo: Repository) -> str | None:
    return clone_auth_for(db, repo.org_id, repo.url) if repo.source == "git" else None


def branch_ref(repo: Repository) -> str:
    if not repo.branch:
        return "HEAD"
    return f"refs/remotes/origin/{repo.branch}" if repo.source == "git" else f"refs/heads/{repo.branch}"


def resolve_checkout(repo: Repository, db: Session | None = None) -> str:
    s = get_settings()
    repo.last_used_at = datetime.now(UTC)
    if repo.source == "local":
        path = str(Path(repo.url).expanduser().resolve())
        if not gitops.is_repo(path):
            raise gitops.GitError(f"{path} is not a git repository")
        return path
    dest = repo.local_path or str(Path(s.repos_dir) / repo.org_id / repo.id)
    auth = _clone_auth(db, repo)
    with checkout_lock(dest):
        if Path(dest, ".git").exists() or gitops.is_repo(dest):
            if repo.branch:
                gitops.fetch_branch(dest, repo.branch, auth)
            else:
                gitops.fetch(dest, auth)
        else:
            gitops.clone(repo.url, dest, auth, branch=repo.branch or None, filter_spec=s.clone_filter)
    repo.local_path = dest
    return dest


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def embed_text(c) -> str:
    head = f"{c.file_path} {c.parent_symbol} {c.symbol_name}" if hasattr(c, "file_path") else ""
    return f"{head}\n{c.content}"[:6000]


def _git_files(path: str, sha: str, max_bytes: int) -> Iterator[tuple[str, str]]:
    for f in gitops.list_files(path, sha):
        try:
            raw = gitops.read_file(path, sha, f)
        except gitops.GitError:
            continue
        if not should_index(f, len(raw), max_bytes) or b"\x00" in raw[:4096]:
            continue
        yield f, raw.decode("utf-8", errors="replace")


def _flag_doc_drift(db: Session, repo: Repository, path: str, prev: str, new: str) -> None:
    from . import drift

    try:
        with db.begin_nested():  # a failure here undoes only the drift work, never the index
            n = drift.detect(db, repo, path, prev, new)
        if n:
            log.info("%s: %d documentation page(s) may be out of date", repo.name, n)
    except Exception:  # an advisory feature must never fail indexing
        log.exception("drift detection failed for %s", repo.name)


def _prefetch(db: Session, repo: Repository, path: str, sha: str, max_bytes: int) -> None:
    try:
        n = gitops.prefetch_blobs(path, sha, lambda f: should_index(f, 0, max_bytes), _clone_auth(db, repo))
        log.info("prefetched %d file contents for %s", n, repo.name)
    except gitops.GitError as e:  # lazy per-file fetching still works, just slower
        log.warning("blob prefetch failed for %s: %s", repo.name, e)


def _doc_files(db: Session, repo: Repository) -> tuple[list[tuple[str, str]], str]:
    docs = list(db.scalars(select(Document).where(Document.repo_id == repo.id).order_by(Document.path)))
    sha = "docs-" + _hash("|".join(f"{d.path}:{d.content_hash}" for d in docs))[:16]
    return [(d.path, d.content) for d in docs], sha


def index_repository(db: Session, repo_id: str, max_commits: int = 300) -> IndexRun:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise ValueError("repository not found")
    run = IndexRun(repo_id=repo.id)
    db.add(run)
    repo.status = "indexing"
    db.commit()
    s = get_settings()
    embedder = Embedder()
    try:
        files: Iterable[tuple[str, str]]
        if repo.source == "docs":
            files, sha = _doc_files(db, repo)
            path = ""
        else:
            path = resolve_checkout(repo, db)
            ref = branch_ref(repo)
            try:
                sha = gitops.head_sha(path, ref)
            except gitops.GitError:
                raise gitops.GitError(f"branch '{repo.branch}' not found in the repository") from None
            repo.web_url = web_base(repo.url if repo.source == "git" else gitops.remote_url(path))
            repo.default_branch = repo.branch or gitops.current_branch(path)
            if repo.source == "git" and gitops.is_partial(path):
                _prefetch(db, repo, path, sha, s.max_file_bytes)
            files = _git_files(path, sha, s.max_file_bytes)
        run.commit_sha = sha
        n_files, kept, written = _sync_chunks(db, repo, run, files, sha, embedder)
        run.chunks_written = written
        run.commits_indexed = _index_commits(db, repo, path, embedder, max_commits, run, ref=sha) if path else 0
        run.files_indexed = n_files
        graph = build_repo_graph(db, repo)
        if path and repo.indexed_sha:
            _flag_doc_drift(db, repo, path, repo.indexed_sha, sha)
        repo.indexed_sha = sha
        repo.file_count = n_files
        repo.chunk_count = kept + written
        repo.status = "ready"
        run.status = "completed"
        if (
            embedding_state.recorded(db, repo.org_id) is None
        ):  # remember what made these vectors; a later change is flagged
            embedding_state.record(db, repo.org_id)
        log.info("indexed %s: %s", repo.name, graph)
    except Exception as e:
        log.exception("indexing failed for %s", repo_id)
        db.rollback()
        repo_f = db.get(Repository, repo_id)
        run_f = db.get(IndexRun, run.id)
        assert repo_f is not None and run_f is not None
        repo, run = repo_f, run_f
        repo.status = "error"
        run.status = "failed"
        run.error = str(e)[:1000]
    run.finished_at = datetime.now(UTC)
    db.commit()
    return run


def _sync_chunks(
    db: Session, repo: Repository, run: IndexRun, files, sha: str, embedder: Embedder
) -> tuple[int, int, int]:
    existing = db.scalars(select(CodeChunk).where(CodeChunk.repo_id == repo.id, CodeChunk.chunk_type != "pr")).all()
    by_key: dict[tuple[str, str, str], list[CodeChunk]] = {}
    for c in existing:
        by_key.setdefault((c.file_path, c.symbol_name, c.content_hash), []).append(c)
    keep: set[str] = set()
    pending: list[CodeChunk] = []
    n_files = 0
    for f, text in files:
        n_files += 1
        lang = detect_language(f)
        for pc in [*parse_file(f, text), *routes.chunks(f, text)]:
            pc.content, red = redact_stored(pc.content)
            run.secrets_redacted += red
            h = _hash(f"{pc.parent}|{pc.symbol}|{pc.content}")
            bucket = by_key.get((f, pc.symbol, h))
            if bucket:
                c = bucket.pop()
                c.commit_sha, c.start_line, c.end_line = sha, pc.start_line, pc.end_line
                c.imports, c.calls = pc.imports, pc.calls
                keep.add(c.id)
                run.chunks_reused += 1
                continue
            c = CodeChunk(
                org_id=repo.org_id,
                repo_id=repo.id,
                commit_sha=sha,
                file_path=f,
                symbol_name=pc.symbol,
                parent_symbol=pc.parent,
                language=lang,
                chunk_type=pc.chunk_type,
                start_line=pc.start_line,
                end_line=pc.end_line,
                content=pc.content,
                content_hash=h,
                imports=pc.imports,
                calls=pc.calls,
            )
            db.add(c)
            pending.append(c)
    for i in range(0, len(pending), 128):
        batch = pending[i : i + 128]
        for c, v in zip(batch, embedder.embed([embed_text(c) for c in batch]), strict=True):
            c.embedding = v
        u = embedder.last_usage
        usage.record_event(
            db,
            repo.org_id,
            agent="indexer",
            provider=embedder.provider,
            model=embedder.model,
            kind="embedding",
            workflow="indexing",
            workflow_id=run.id,
            input_tokens=u.get("input_tokens", 0),
            latency_ms=u.get("latency_ms", 0),
            usage_source=u.get("usage_source", "local"),
        )
    stale = [c.id for c in existing if c.id not in keep]
    for i in range(0, len(stale), 500):
        db.execute(delete(CodeChunk).where(CodeChunk.id.in_(stale[i : i + 500])))
    db.flush()
    return n_files, len(keep), len(pending)


def _safe_diff(path: str, sha: str, run: IndexRun | None) -> str:
    text, n = redact_stored(gitops.diff_excerpt(path, sha))
    if run is not None:
        run.secrets_redacted += n
    return text


def _index_commits(
    db: Session,
    repo: Repository,
    path: str,
    embedder: Embedder,
    limit: int,
    run: IndexRun | None = None,
    ref: str = "HEAD",
) -> int:
    known = set(db.scalars(select(Change.sha).where(Change.repo_id == repo.id)).all())
    commits = [c for c in gitops.log(path, limit=limit, ref=ref) if c.sha not in known]
    if not commits:
        return 0
    rows = []
    for c in commits:
        rows.append(
            Change(
                org_id=repo.org_id,
                repo_id=repo.id,
                sha=c.sha,
                parent_sha=c.parent,
                author=c.author,
                committed_at=c.committed_at,
                message=redact_stored(c.message)[0],
                files=c.files,
                diff_excerpt=_safe_diff(path, c.sha, run),
            )
        )
    texts = [f"{r.message}\n" + " ".join(f["path"] for f in r.files) for r in rows]
    for r, v in zip(rows, embedder.embed(texts), strict=True):
        r.embedding = v
    db.add_all(rows)
    return len(rows)
