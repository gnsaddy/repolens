"""Local memory of answers the model already gave.

A repeated or re-worded question is matched by embedding similarity (computed locally) and answered from the
stored result at zero token cost. An entry is only used while the indexed sources it relied on are unchanged, so a
re-index or wiki edit quietly retires it. Entries are keyed by the set of repositories the asker may see, so one
person's answer is never served to someone with different access.
"""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AnswerMemory, Document, Repository, _now
from . import embeddings, usage
from .access import Scope


def _embed(text: str) -> list[float]:
    return embeddings.Embedder().embed([text])[0]


def scope_key(scope: Scope | None, repo_ids: list[str] | None, kinds: list[str] | None) -> str:
    seen = None if scope is None or scope.allowed is None else sorted(scope.allowed)
    raw = json.dumps([seen, sorted(repo_ids or []), sorted(kinds or [])])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def fingerprint(db: Session, org: str, scope: Scope | None, repo_ids: list[str] | None) -> str:
    """Changes whenever anything the answer could have drawn on is re-indexed, added or removed."""
    q = select(Repository.id, Repository.indexed_sha, Repository.chunk_count, Repository.file_count).where(
        Repository.org_id == org
    )
    if repo_ids:
        q = q.where(Repository.id.in_(repo_ids))
    rows = sorted(
        (r.id, r.indexed_sha or "", r.chunk_count, r.file_count) for r in db.execute(q) if not scope or scope.can(r.id)
    )
    dq = select(Document.repo_id, Document.path, Document.content_hash).where(Document.org_id == org)
    if repo_ids:
        dq = dq.where(Document.repo_id.in_(repo_ids))
    docs = sorted(tuple(d) for d in db.execute(dq) if not scope or scope.can(d.repo_id))  # uploaded / wiki pages
    return hashlib.sha256(json.dumps([rows, docs]).encode()).hexdigest()[:32]


def enabled() -> bool:
    return get_settings().answer_memory_enabled


def lookup(
    db: Session, org: str, kind: str, query: str, key: str, fp: str, *, provider: str = "", model: str = ""
) -> dict | None:
    """The stored answer for a question that means the same thing, or None."""
    if not enabled():
        return None
    s = get_settings()
    cutoff = _now() - timedelta(hours=s.answer_memory_ttl_hours)
    rows = db.scalars(
        select(AnswerMemory).where(
            AnswerMemory.org_id == org,
            AnswerMemory.kind == kind,
            AnswerMemory.scope_key == key,
            AnswerMemory.created_at >= cutoff,
        )
    ).all()
    if not rows:
        return None
    qv = _embed(query)
    best, score = None, 0.0
    for r in rows:
        if r.source_state.get("fp") != fp or not r.embedding:
            continue
        c = embeddings.cosine(qv, r.embedding)
        if c > score:
            best, score = r, c
    if best is None or score < s.answer_memory_threshold:
        return None
    best.hits += 1
    best.tokens_saved += best.tokens
    best.last_hit_at = _now()
    usage.record_event(
        db,
        org,
        agent="memory",
        provider=provider or "local",
        model=model or "memory",
        workflow="knowledge",
        cache_hit=True,
        tokens_avoided=best.tokens,
        usage_source="estimated",
    )
    db.commit()
    return {**best.answer, "memory": {"similarity": round(score, 3), "asked": best.query, "tokens_saved": best.tokens}}


def remember(db: Session, org: str, kind: str, query: str, key: str, fp: str, answer: dict, tokens: int) -> None:
    if not enabled():
        return
    s = get_settings()
    db.add(
        AnswerMemory(
            org_id=org,
            kind=kind,
            scope_key=key,
            query=query[:2000],
            embedding=_embed(query),
            answer=answer,
            source_state={"fp": fp},
            tokens=tokens,
        )
    )
    # keep the memory bounded: drop the oldest beyond the cap
    n = db.scalar(select(func.count()).select_from(AnswerMemory).where(AnswerMemory.org_id == org)) or 0
    if n > s.answer_memory_max_entries:
        old = db.scalars(
            select(AnswerMemory.id)
            .where(AnswerMemory.org_id == org)
            .order_by(AnswerMemory.created_at)
            .limit(n - s.answer_memory_max_entries)
        ).all()
        db.execute(delete(AnswerMemory).where(AnswerMemory.id.in_(old)))
    db.commit()


def stats(db: Session, org: str) -> dict:
    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AnswerMemory.hits), 0),
            func.coalesce(func.sum(AnswerMemory.tokens_saved), 0),
        ).where(AnswerMemory.org_id == org)
    ).one()
    s = get_settings()
    return {
        "enabled": s.answer_memory_enabled,
        "entries": row[0],
        "hits": int(row[1]),
        "tokens_saved": int(row[2]),
        "threshold": s.answer_memory_threshold,
        "ttl_hours": s.answer_memory_ttl_hours,
    }


def clear(db: Session, org: str) -> int:
    n = db.scalar(select(func.count()).select_from(AnswerMemory).where(AnswerMemory.org_id == org)) or 0
    db.execute(delete(AnswerMemory).where(AnswerMemory.org_id == org))
    db.commit()
    return n
