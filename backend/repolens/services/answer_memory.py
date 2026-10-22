"""Local memory of answers the model already gave.

A repeated or re-worded question is matched by embedding similarity (computed locally) and answered from the
stored result at zero token cost. An entry is only used while the indexed sources it relied on are unchanged, so a
re-index or wiki edit quietly retires it. Entries are keyed by the set of repositories the asker may see, so one
person's answer is never served to someone with different access.
"""

import hashlib
import json
from datetime import UTC, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AnswerMemory, Document, Repository, _now
from . import embeddings, usage
from .access import Scope


def _embed(text: str) -> list[float]:
    return embeddings.Embedder().embed([text])[0]


_STOP = frozenset(
    "a an the of to in on for and or is are was were be been do does did how what why when where which who whom "
    "can could should would will with by at from as it its this that these those i we you my our your me us there "
    "any about into than then so if not no".split()
)


def _words(text: str) -> frozenset[str]:
    """Content words of a question, lightly stemmed, so re-wordings of it compare as nearly equal."""
    return frozenset(embeddings.stem(w) for w in (t.lower() for t in embeddings.tokenize(text)) if w not in _STOP)


def _similarity(qv: list[float], qw: frozenset[str], row: AnswerMemory) -> tuple[float, float]:
    """(embedding cosine, word overlap) between a new question and a remembered one."""
    cos = embeddings.cosine(qv, row.embedding) if row.embedding else 0.0
    other = _words(row.query)
    jac = len(qw & other) / len(qw | other) if qw and other else 0.0
    return cos, jac


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
    qv, qw = _embed(query), _words(query)
    best, score = None, 0.0
    for r in rows:
        if r.source_state.get("fp") != fp or not r.embedding:
            continue
        cos, jac = _similarity(qv, qw, r)
        if (cos >= s.answer_memory_threshold or jac >= s.answer_memory_word_threshold) and max(cos, jac) > score:
            best, score = r, max(cos, jac)
    if best is None:
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


def similar(
    db: Session, org: str, kind: str, query: str, key: str, fp: str, limit: int = 3, floor: float = 0.5
) -> list[dict]:
    """Earlier questions that are close to this one, best first, so they can be suggested before the model answers."""
    if not enabled():
        return []
    s = get_settings()
    cutoff = _now() - timedelta(hours=s.answer_memory_ttl_hours)
    qv, qw = _embed(query), _words(query)
    out = []
    for r in db.scalars(
        select(AnswerMemory).where(
            AnswerMemory.org_id == org,
            AnswerMemory.kind == kind,
            AnswerMemory.scope_key == key,
            AnswerMemory.created_at >= cutoff,
        )
    ):
        if r.source_state.get("fp") != fp:  # the sources changed since; the old answer may no longer hold
            continue
        cos, jac = _similarity(qv, qw, r)
        score = max(cos, jac)
        if score >= floor:
            out.append(
                {
                    "id": r.id,
                    "asked": r.query,
                    "similarity": round(score, 2),
                    "answer": r.answer.get("answer", ""),
                    "sources": [x.get("file") for x in r.answer.get("sources", [])][:4],
                    "age_hours": int(
                        (
                            _now() - (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC))
                        ).total_seconds()
                        // 3600
                    ),
                    "hits": r.hits,
                }
            )
    return sorted(out, key=lambda x: -x["similarity"])[:limit]


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


def suggest(
    db: Session,
    org: str,
    kind: str,
    query: str,
    scope: Scope | None,
    repo_ids: list[str] | None,
    kinds: list[str] | None,
) -> list[dict]:
    return similar(db, org, kind, query, scope_key(scope, repo_ids, kinds), fingerprint(db, org, scope, repo_ids))
