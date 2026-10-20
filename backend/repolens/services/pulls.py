"""Pull-request ingestion: PR text becomes searchable chunks and graph nodes linked to commits, files and symbols."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Change, CodeChunk, KGEdge, KGNode, PullRequest, Repository
from . import usage
from .docs import extract_mentions
from .embeddings import Embedder
from .github import GitHubClient, normalize_pull
from .graph import edge_id, node_id, refresh_degrees


def pr_chunk_text(pr: PullRequest) -> str:
    files = ", ".join(f["path"] for f in pr.files[:40])
    comments = "\n".join(f"- {c['author']}: {c['body']}" for c in pr.comments[:20])
    return (
        f"PR #{pr.number}: {pr.title}\nState: {pr.state}. Author: {pr.author}. "
        f"Branch: {pr.head_ref} -> {pr.base_ref}\n\n{pr.body}\n\nFiles changed: {files}\n\nDiscussion:\n{comments}"
    ).strip()


def sync_pulls(
    db: Session, repo: Repository, client: GitHubClient, gh_repo: str, limit: int = 100, only: list[int] | None = None
) -> dict:
    """Fetch PRs from GitHub (read-only) and index them. Returns counts."""
    if only:
        raws = [client.get_pull(gh_repo, n) for n in only]
    else:
        raws = client.list_pulls(gh_repo, limit)
    embedder = Embedder()
    synced = 0
    for raw in raws:
        if not raw or "number" not in raw:
            continue
        n = raw["number"]
        data = normalize_pull(
            raw, client.pull_files(gh_repo, n), client.pull_commits(gh_repo, n), client.pull_comments(gh_repo, n)
        )
        row = db.scalar(select(PullRequest).where(PullRequest.repo_id == repo.id, PullRequest.number == n))
        if row is None:
            row = PullRequest(org_id=repo.org_id, repo_id=repo.id, **data)
            db.add(row)
        else:
            for k, v in data.items():
                setattr(row, k, v)
        row.synced_at = datetime.now(UTC)
        db.flush()
        chunk = _upsert_chunk(db, repo, row, embedder)
        _upsert_graph(db, repo, row, chunk)
        for ch in db.scalars(select(Change).where(Change.repo_id == repo.id, Change.sha.in_(row.commit_shas or ["-"]))):
            ch.pr_number, ch.pr_title = row.number, row.title[:500]
        db.commit()
        synced += 1
    refresh_degrees(db, repo.org_id, repo.id)
    db.commit()
    return {"synced": synced}


def _upsert_chunk(db: Session, repo: Repository, pr: PullRequest, embedder: Embedder) -> CodeChunk:
    path = f"pull/{pr.number}"
    text = pr_chunk_text(pr)
    h = hashlib.sha256(text.encode()).hexdigest()
    existing = db.scalar(select(CodeChunk).where(CodeChunk.repo_id == repo.id, CodeChunk.file_path == path))
    if existing is not None and existing.content_hash == h:
        return existing
    if existing is not None:
        db.delete(existing)
        db.flush()
    chunk = CodeChunk(
        org_id=repo.org_id,
        repo_id=repo.id,
        commit_sha=pr.merge_commit_sha or "pr",
        file_path=path,
        symbol_name=pr.title[:300],
        parent_symbol=f"#{pr.number}",
        language="pr",
        chunk_type="pr",
        start_line=1,
        end_line=max(1, len(text.splitlines())),
        content=text[:20000],
        content_hash=h,
        imports=[],
        calls=extract_mentions(text),
        embedding=embedder.embed([text[:6000]])[0],
    )
    db.add(chunk)
    u = embedder.last_usage
    usage.record_event(
        db,
        repo.org_id,
        agent="indexer",
        provider=embedder.provider,
        model=embedder.model,
        kind="embedding",
        workflow="pr_ingestion",
        input_tokens=u.get("input_tokens", 0),
        latency_ms=u.get("latency_ms", 0),
        usage_source=u.get("usage_source", "local"),
    )
    db.flush()
    return chunk


def _upsert_graph(db: Session, repo: Repository, pr: PullRequest, chunk: CodeChunk) -> None:
    org, rid = repo.org_id, repo.id
    nid = node_id(org, rid, "pr", str(pr.number))
    meta = {
        "number": pr.number,
        "state": pr.state,
        "author": pr.author,
        "url": pr.url,
        "jira_keys": pr.jira_keys,
        "labels": pr.labels,
    }
    node = db.get(KGNode, nid)
    name = f"#{pr.number} {pr.title}"[:500]
    if node is None:
        db.add(
            KGNode(
                id=nid,
                org_id=org,
                repo_id=rid,
                type="pr",
                name=name,
                qualname=f"#{pr.number}",
                path=f"pull/{pr.number}",
                chunk_id=chunk.id,
                meta=meta,
            )
        )
    else:
        node.name, node.chunk_id, node.meta = name, chunk.id, meta
    db.flush()
    db.execute(delete(KGEdge).where(KGEdge.src_id == nid, KGEdge.provenance == "github"))
    edges: dict[str, KGEdge] = {}

    def add(dst: str, rel: str, w: float, ev: str) -> None:
        eid = edge_id(nid, dst, rel)
        edges.setdefault(
            eid,
            KGEdge(
                id=eid,
                org_id=org,
                repo_id=rid,
                src_id=nid,
                dst_id=dst,
                rel=rel,
                provenance="github",
                weight=w,
                evidence=ev[:400],
            ),
        )

    # commits included in the PR (+ merge commit)
    shas = list(dict.fromkeys([*pr.commit_shas, pr.merge_commit_sha] if pr.merge_commit_sha else pr.commit_shas))
    for sha in shas:
        cid = node_id(org, rid, "commit", sha)
        if db.get(KGNode, cid) is not None:
            add(cid, "INCLUDES", 1.0, f"commit {sha[:8]} is part of PR #{pr.number}")
    # files touched
    paths = [f["path"] for f in pr.files]
    file_nodes = {
        n.path: n
        for n in db.scalars(
            select(KGNode).where(
                KGNode.repo_id == rid, KGNode.type.in_(("file", "doc")), KGNode.path.in_(paths or ["-"])
            )
        )
    }
    for p, fn in file_nodes.items():
        add(fn.id, "MODIFIES", 1.0, f"PR #{pr.number} changes {p}")
    # symbols / docs the PR text refers to (unique names only)
    by_name: dict[str, list[KGNode]] = {}
    for n in db.scalars(select(KGNode).where(KGNode.repo_id == rid, KGNode.type.in_(("function", "class")))):
        by_name.setdefault(n.name, []).append(n)
    for tok in chunk.calls or []:
        cands = by_name.get(tok.split(".")[-1], [])
        if len(cands) == 1:
            add(cands[0].id, "MENTIONS", 0.7, f"PR #{pr.number} mentions `{tok}`")
    db.add_all(edges.values())
    db.flush()
