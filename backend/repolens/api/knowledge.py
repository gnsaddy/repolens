import hashlib
import math
import posixpath
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Change, CodeChunk, Document, KGEdge, KGNode, Repository
from ..schemas import CollectionCreate, DocsUpload, HeatIn, KnowledgeQuery, RepoOut
from ..security import redact_stored
from ..services import answer_memory
from ..services import graph as g
from ..services import knowledge as kb
from ..services.access import scope_for
from ..services.policy import authorize
from .repos import _get_repo, schedule_index

router = APIRouter(prefix="/api", tags=["knowledge"])
DOC_EXT = (".md", ".mdx", ".rst", ".txt", ".adoc")


def _names(db: Session, p: Principal) -> dict[str, str]:
    scope = scope_for(db, p)
    return {
        r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id)) if scope.can(r.id)
    }


def _node(db: Session, p: Principal, node_id: str) -> KGNode:
    n = db.get(KGNode, node_id)
    if n is None or n.org_id != p.org_id or not scope_for(db, p).can(n.repo_id):
        raise HTTPException(404, "Node not found")
    return n


# ---------------------------------------------------------------------------------------------- collections/docs
@router.post("/knowledge/collections", response_model=RepoOut, status_code=201)
def create_collection(body: CollectionCreate, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "collection.create", body.name)
    if db.scalar(select(Repository).where(Repository.org_id == p.org_id, Repository.name == body.name)):
        raise HTTPException(409, "A repository or collection with that name already exists")
    repo = Repository(org_id=p.org_id, name=body.name, source="docs", url="", status="ready")
    db.add(repo)
    db.commit()
    return repo


def _clean_path(path: str) -> str:
    norm = posixpath.normpath(path.replace("\\", "/").lstrip("/"))
    if norm.startswith("..") or "/../" in f"/{norm}/" or norm in (".", ""):
        raise HTTPException(400, f"Invalid document path: {path}")
    if not norm.lower().endswith(DOC_EXT):
        raise HTTPException(400, f"Unsupported file type (allowed: {', '.join(DOC_EXT)}): {path}")
    return norm


@router.post("/knowledge/collections/{repo_id}/documents")
def upload_documents(
    repo_id: str, body: DocsUpload, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    repo = _get_repo(db, p, repo_id)
    if repo.source != "docs":
        raise HTTPException(400, "Documents can only be uploaded to a docs collection")
    authorize(db, p, "document.write", repo.name, count=len(body.documents))
    changed = 0
    redacted = 0
    for d in body.documents:
        path = _clean_path(d.path)
        if "\x00" in d.content:
            raise HTTPException(400, f"Binary content rejected: {path}")
        content, n_red = redact_stored(d.content)  # secrets never enter the knowledge base
        redacted += n_red
        h = hashlib.sha256(content.encode()).hexdigest()
        row = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == path))
        if row is None:
            db.add(
                Document(
                    org_id=p.org_id,
                    repo_id=repo.id,
                    path=path,
                    title=posixpath.basename(path),
                    content=content,
                    content_hash=h,
                    created_by=p.user_id,
                )
            )
            changed += 1
        elif row.content_hash != h:
            row.content, row.content_hash = content, h
            changed += 1
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)
    return {"received": len(body.documents), "changed": changed, "secrets_redacted": redacted}


@router.get("/knowledge/collections/{repo_id}/documents")
def list_documents(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _get_repo(db, p, repo_id)
    rows = db.scalars(select(Document).where(Document.repo_id == repo_id).order_by(Document.path))
    return [
        {
            "id": d.id,
            "path": d.path,
            "size": len(d.content),
            "updated_at": d.updated_at,
            "source_type": d.source_type,
            "deletable": not managed_by_sync(d),
        }
        for d in rows
    ]


@router.delete("/knowledge/collections/{repo_id}", status_code=204)
def delete_collection(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Delete a documentation collection with all its documents, chunks and graph nodes."""
    from .repos import purge_repository

    repo = _get_repo(db, p, repo_id)
    if repo.source != "docs":
        raise HTTPException(400, "Only documentation collections can be deleted here")
    authorize(db, p, "collection.delete", repo.name)
    purge_repository(db, repo)


def managed_by_sync(doc: Document) -> bool:
    """Pages owned by a connected source (e.g. Confluence) return on the next sync, so they are not deleted by hand."""
    return doc.source_type not in ("upload", "url", "")


@router.delete("/knowledge/collections/{repo_id}/documents/{doc_id}", status_code=204)
def delete_document(
    repo_id: str, doc_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    repo = _get_repo(db, p, repo_id)
    authorize(db, p, "document.delete", doc_id)
    doc = db.get(Document, doc_id)
    if doc is None or doc.repo_id != repo.id:
        raise HTTPException(404, "Document not found")
    if managed_by_sync(doc):
        raise HTTPException(409, f"This page is synced from {doc.source_type}. Remove the source instead.")
    db.delete(doc)
    repo.status = "indexing"
    db.commit()
    schedule_index(repo.id)


# ---------------------------------------------------------------------------------------------- search / ask
@router.post("/knowledge/search")
def knowledge_search(body: KnowledgeQuery, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    ids = scope.resolve(body.repo_ids)
    return kb.search(db, p.org_id, body.query, ids or None, k=body.k, kinds=body.kinds or None, scope=scope)


@router.post("/knowledge/ask")
def knowledge_ask(body: KnowledgeQuery, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    ids = scope.resolve(body.repo_ids)
    return kb.ask(db, p.org_id, body.query, ids or None, k=min(body.k, 10), scope=scope, fresh=body.fresh)


@router.post("/knowledge/similar")
def knowledge_similar(body: KnowledgeQuery, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Questions already answered that are close to this one. Free and instant: nothing is sent to a model."""
    scope = scope_for(db, p)
    ids = scope.resolve(body.repo_ids)
    return answer_memory.suggest(db, p.org_id, "ask", body.query, scope, ids or None, None)


# ---------------------------------------------------------------------------------------------- graph
@router.get("/graph/stats")
def graph_stats(repo_id: str | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if repo_id:
        _get_repo(db, p, repo_id)
    ids = scope_for(db, p).ids_or_none()
    nq = select(KGNode.type, func.count()).where(KGNode.org_id == p.org_id).group_by(KGNode.type)
    eq = (
        select(KGEdge.rel, KGEdge.provenance, func.count())
        .where(KGEdge.org_id == p.org_id)
        .group_by(KGEdge.rel, KGEdge.provenance)
    )
    if ids is not None:  # restricted viewer: count only nodes they may see and edges between such nodes
        visible = select(KGNode.id).where(KGNode.repo_id.in_(ids))
        nq = nq.where(KGNode.repo_id.in_(ids))
        eq = eq.where(KGEdge.src_id.in_(visible), KGEdge.dst_id.in_(visible))
    if repo_id:
        nq = nq.where(KGNode.repo_id == repo_id)
        eq = eq.where(KGEdge.repo_id == repo_id)
    edges = [{"rel": r, "provenance": pv, "count": c} for r, pv, c in db.execute(eq)]
    return {
        "nodes": {t: c for t, c in db.execute(nq)},
        "edges": edges,
        "total_nodes": sum(c for t, c in db.execute(nq)),
        "total_edges": sum(e["count"] for e in edges),
    }


@router.get("/graph/overview")
def graph_overview(
    repo_id: str | None = None,
    types: str | None = None,
    rels: str | None = None,
    limit: int = Query(120, ge=10, le=400),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Most-connected nodes (commits hidden unless requested) and the edges among them."""
    if repo_id:
        _get_repo(db, p, repo_id)
    wanted = set(types.split(",")) if types else {"file", "doc", "section", "function", "class"}
    q = select(KGNode).where(KGNode.org_id == p.org_id, KGNode.type.in_(wanted), KGNode.degree > 0)
    visible_ids = scope_for(db, p).ids_or_none()
    if visible_ids is not None:
        q = q.where(KGNode.repo_id.in_(visible_ids))
    if repo_id:
        q = q.where(KGNode.repo_id == repo_id)
    nodes = list(db.scalars(q.order_by(KGNode.degree.desc()).limit(limit)))
    ids = [n.id for n in nodes]
    eq = select(KGEdge).where(KGEdge.org_id == p.org_id, KGEdge.src_id.in_(ids), KGEdge.dst_id.in_(ids))
    if rels:
        eq = eq.where(KGEdge.rel.in_(rels.split(",")))
    names = _names(db, p)
    return {"nodes": [g.node_dict(n, names) for n in nodes], "edges": [g.edge_dict(e) for e in db.scalars(eq)]}


@router.get("/graph/nodes")
def graph_nodes(
    q: str = Query(min_length=1, max_length=200),
    type: str | None = None,
    limit: int = Query(20, le=100),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    like = f"%{q.lower()}%"
    stmt = select(KGNode).where(
        KGNode.org_id == p.org_id,
        or_(
            func.lower(KGNode.name).like(like),
            func.lower(KGNode.qualname).like(like),
            func.lower(KGNode.path).like(like),
        ),
    )
    if type:
        stmt = stmt.where(KGNode.type == type)
    visible_ids = scope_for(db, p).ids_or_none()
    if visible_ids is not None:
        stmt = stmt.where(KGNode.repo_id.in_(visible_ids))
    names = _names(db, p)
    return [g.node_dict(n, names) for n in db.scalars(stmt.order_by(KGNode.degree.desc()).limit(limit))]


@router.get("/graph/nodes/{node_id}")
def graph_node(
    node_id: str,
    depth: int = Query(1, ge=1, le=3),
    rels: str | None = None,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    n = _node(db, p, node_id)
    scope = scope_for(db, p)
    es, ns = g.subgraph(db, p.org_id, [n.id], depth, rels.split(",") if rels else None, max_nodes=150, scope=scope)
    names = _names(db, p)
    chunk = db.get(CodeChunk, n.chunk_id) if n.chunk_id else None
    return {
        "node": g.node_dict(n, names),
        "content": chunk.content[:4000] if chunk else "",
        "nodes": [g.node_dict(x, names) for x in ns],
        "edges": [g.edge_dict(e) for e in es],
        "connections": kb._connections(db, p.org_id, n, names, limit=25, scope=scope),
    }


@router.get("/graph/nodes/{node_id}/brief")
def graph_brief(node_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What to know before touching this symbol: callers, dependencies, docs, history and warnings."""
    n = _node(db, p, node_id)
    names = _names(db, p)
    conns = kb._connections(db, p.org_id, n, names, limit=60, scope=scope_for(db, p))
    return g.brief(n, conns, names.get(n.repo_id, ""))


_FIX_RE = re.compile(r"\b(fix(es|ed)?|bug|hotfix|revert|regression|crash|patch)\b", re.I)
HEAT_LABELS = {
    "churn": "Lines changed recently",
    "bugs": "Lines changed by fix/revert commits",
    "connections": "Number of connections",
    "undocumented": "Public code no document mentions",
}


@router.post("/graph/heat")
def graph_heat(body: HeatIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Heat (0..1) per node: where change, bugs, coupling or documentation gaps concentrate."""
    scope = scope_for(db, p)
    nodes = [
        n
        for n in db.scalars(select(KGNode).where(KGNode.id.in_(body.node_ids), KGNode.org_id == p.org_id))
        if scope.can(n.repo_id)
    ]
    raw: dict[str, float] = {}
    if body.metric == "connections":
        raw = {n.id: float(n.degree) for n in nodes}
    elif body.metric == "undocumented":
        ids = [n.id for n in nodes if n.type in ("function", "class")]
        mentioned = set(
            db.scalars(
                select(KGEdge.dst_id).where(KGEdge.dst_id.in_(ids), KGEdge.rel == "MENTIONS", KGEdge.org_id == p.org_id)
            )
        )
        raw = {i: 0.0 if i in mentioned else 1.0 for i in ids}
    else:
        cutoff = datetime.now(UTC) - timedelta(days=body.days)
        repo_ids = {n.repo_id for n in nodes}
        lines: dict[tuple[str, str], float] = {}
        for ch in db.scalars(select(Change).where(Change.repo_id.in_(repo_ids), Change.committed_at >= cutoff)):
            if body.metric == "bugs" and not _FIX_RE.search(ch.message or ""):
                continue
            for f in ch.files or []:
                key = (ch.repo_id, f.get("path", ""))
                lines[key] = lines.get(key, 0.0) + f.get("additions", 0) + f.get("deletions", 0)
        raw = {n.id: lines[(n.repo_id, n.path)] for n in nodes if (n.repo_id, n.path) in lines}
    top = max(raw.values(), default=0.0)
    scale = math.log1p(top) or 1.0
    values = {i: round(math.log1p(v) / scale, 3) for i, v in raw.items() if top > 0 and body.metric != "undocumented"}
    if body.metric == "undocumented":
        values = dict(raw)
    return {"metric": body.metric, "label": HEAT_LABELS[body.metric], "max": top, "values": values, "raw": raw}


@router.get("/graph/path")
def graph_path(src: str, dst: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _node(db, p, src)
    _node(db, p, dst)
    steps = g.find_path(db, p.org_id, src, dst, scope=scope_for(db, p))
    nodes = {n.id: n for n in db.scalars(select(KGNode).where(KGNode.id.in_([s for s, _ in steps])))}
    names = _names(db, p)
    return {
        "found": bool(steps),
        "steps": [{"node": g.node_dict(nodes[i], names), "edge": g.edge_dict(e) if e else None} for i, e in steps],
    }


def delete_repo_graph(db: Session, repo_id: str) -> None:
    ids = list(db.scalars(select(KGNode.id).where(KGNode.repo_id == repo_id)))
    for i in range(0, len(ids), 500):
        b = ids[i : i + 500]
        db.execute(delete(KGEdge).where(or_(KGEdge.src_id.in_(b), KGEdge.dst_id.in_(b))))
    db.execute(delete(KGEdge).where(KGEdge.repo_id == repo_id))
    db.execute(delete(KGNode).where(KGNode.repo_id == repo_id))
    db.execute(delete(Document).where(Document.repo_id == repo_id))
