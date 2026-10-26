"""Local memory of answers the model already gave.

A repeated or re-worded question is matched by embedding similarity (computed locally) and answered from the
stored result at zero token cost. An entry is only used while the indexed sources it relied on are unchanged, so a
re-index or wiki edit quietly retires it. Entries are keyed by the set of repositories the asker may see, so one
person's answer is never served to someone with different access.
"""

import hashlib
import json
from datetime import UTC, timedelta
from functools import lru_cache

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AnswerMemory, Document, Repository, _now
from . import embeddings, usage
from .access import Scope


@lru_cache(maxsize=512)
def _embed_cached(text: str, model: str) -> tuple[float, ...]:
    return tuple(embeddings.Embedder().embed([text])[0])


def _embed(text: str) -> list[float]:
    """Embed a question once per process: repeats and the lookup-then-store pair cost one embedding, not two."""
    return list(_embed_cached(text, embeddings.Embedder().model))


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


PIPELINE = "p3"  # bump when answers are produced differently, so older ones are never replayed or suggested


def scope_key(scope: Scope | None, repo_ids: list[str] | None, kinds: list[str] | None) -> str:
    seen = None if scope is None or scope.allowed is None else sorted(scope.allowed)
    raw = json.dumps([PIPELINE, seen, sorted(repo_ids or []), sorted(kinds or [])])
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


# ------------------------------------------------------------------------------------------------ browse and skills
def _visible(db: Session, org: str, scope: Scope | None, admin: bool) -> list[AnswerMemory]:
    """Entries a person may read. Admins see everything in the workspace; everyone else only answers that were
    produced for the repositories they can see (the two standard questions: all sources, and documentation only)."""
    q = select(AnswerMemory).where(AnswerMemory.org_id == org).order_by(AnswerMemory.created_at.desc()).limit(1000)
    rows = list(db.scalars(q))
    if admin:
        return rows
    keys = {scope_key(scope, None, None), scope_key(scope, None, ["doc"])}
    return [r for r in rows if r.scope_key in keys]


def _view(r: AnswerMemory, score: float | None = None, matched: str = "") -> dict:
    return {
        "id": r.id,
        "asked": r.query,
        "answer": r.answer.get("answer", ""),
        "sources": sorted({s.get("file") for s in r.answer.get("sources", []) if s.get("file")})[:6],
        "hits": r.hits,
        "tokens": r.tokens,
        "tokens_saved": r.tokens_saved,
        "created_at": r.created_at,
        "last_hit_at": r.last_hit_at,
        "kind": r.kind,
        **({"score": round(score, 2), "matched": matched} if score is not None else {}),
    }


def browse(
    db: Session, org: str, scope: Scope | None, admin: bool, q: str = "", deep: bool = True, limit: int = 40
) -> list[dict]:
    """List remembered answers. With a query, rank them by how well the question and, when `deep`, the answer text
    match: embedding similarity of the question, plus how many of the query's words appear anywhere in the entry."""
    rows = _visible(db, org, scope, admin)
    if not q.strip():
        return [_view(r) for r in rows[:limit]]
    qv, qw = _embed(q), _words(q)
    ranked: list[tuple[float, str, AnswerMemory]] = []
    for r in rows:
        cos, jac = _similarity(qv, qw, r)
        score, where = max(cos, jac), "question"
        if deep and qw:
            body = _words(r.answer.get("answer", ""))
            cover = len(qw & (body | _words(r.query))) / len(qw)  # share of the query's words found in the entry
            if cover * 0.9 > score:
                score, where = cover * 0.9, "answer"
        if score >= 0.3:
            ranked.append((score, where, r))
    ranked.sort(key=lambda t: -t[0])
    return [_view(r, s, w) for s, w, r in ranked[:limit]]


def delete_entry(db: Session, org: str, entry_id: str) -> bool:
    row = db.get(AnswerMemory, entry_id)
    if row is None or row.org_id != org:
        return False
    db.delete(row)
    db.commit()
    return True


def build_skill(db: Session, org: str, scope: Scope | None, admin: bool, ids: list[str], name: str = "") -> dict:
    """Turn remembered answers into a Claude skill (SKILL.md): when to use it, the answers and their sources."""
    import re as _re

    from ..security import redact_stored

    allowed = {r.id: r for r in _visible(db, org, scope, admin)}
    rows = [allowed[i] for i in ids if i in allowed]
    if not rows:
        raise ValueError("None of the selected answers are available")
    first = rows[0].query
    slug = _re.sub(r"[^a-z0-9]+", "-", (name or first).lower()).strip("-")[:60].strip("-") or "repolens-knowledge"
    topics = "; ".join(r.query.rstrip("?. ") for r in rows[:6])
    desc = f"Answers about: {topics}. Use when the user asks about these topics or something close to them."[:1000]
    out = [f"---\nname: {slug}\ndescription: {desc}\n---\n", f"# {first.rstrip('?. ').capitalize()}\n"]
    out.append(
        "These answers were produced by RepoLens AI from this team's documents, pull requests and code, "
        "and reviewed by reuse. Check the sources before relying on them for a change.\n"
    )
    out.append("## When to use\n\n" + "\n".join(f"- {r.query}" for r in rows) + "\n")
    out.append("## Answers\n")
    for r in rows:
        files = sorted({s.get("file") for s in r.answer.get("sources", []) if s.get("file")})
        block = f"### {r.query}\n\n{r.answer.get('answer', '').strip()}\n"
        if files:
            block += "\nSources: " + ", ".join(f"`{f}`" for f in files[:6]) + "\n"
        out.append(block)
    text, _ = redact_stored("\n".join(out))  # secrets never leave in an exported skill
    return {"name": slug, "filename": f"{slug}/SKILL.md", "markdown": text, "entries": len(rows)}
