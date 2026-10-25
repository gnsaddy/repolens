"""Hybrid retrieval: vector similarity + lexical match + symbol match, fused with RRF."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Change, CodeChunk, MemoryEntry
from .embeddings import Embedder, cosine, tokenize
from .rerank import rerank as rerank_scores

STOP = set(
    """a an the and or of to in on for with is are was were be been it this that at by from as not no but if
then when after before after during error issue bug fails failing failed failure occurs happens does doesnt
cannot cant unable intermittently intermittent started since yesterday today deployment user users""".split()
)

RRF_K = 60


@dataclass
class Hit:
    chunk: CodeChunk
    score: float
    vector_rank: int | None = None
    lexical_rank: int | None = None
    symbol_match: bool = False
    matched_terms: list[str] = field(default_factory=list)


def _pos(c: CodeChunk) -> tuple[str, int]:
    """Deterministic tie-break: equal scores order by file and line, never by random row ids."""
    return (c.file_path, c.start_line)


def query_terms(q: str) -> list[str]:
    seen: list[str] = []
    for t in tokenize(q):
        if len(t) > 2 and t not in STOP and t not in seen:
            seen.append(t)
    return seen


def extract_stack_refs(text_: str) -> list[dict]:
    """Pull file:line references and symbol names out of stack traces / error text.

    `innermost` marks the frame where the failure happened: the LAST frame of a Python traceback (outermost first),
    the FIRST frame of JavaScript/Java traces (innermost first).
    """
    refs: list[dict] = []
    py = [
        {"file": m.group(1), "line": int(m.group(2)), "symbol": m.group(3)}
        for m in re.finditer(r'File "([^"]+)", line (\d+), in (\w+)', text_)
    ]
    if py:
        py[-1]["innermost"] = True
    refs += py
    js = [
        {"file": m.group(2), "line": int(m.group(3)), "symbol": m.group(1)}
        for m in re.finditer(r"at (?:[\w$.<>]+\.)?([\w$<>]+) \(?([\w./\\-]+\.\w+):(\d+)", text_)
    ]
    java = [
        {"file": m.group(1), "line": int(m.group(2)), "symbol": ""}
        for m in re.finditer(r"\(([\w]+\.java):(\d+)\)", text_)
    ]
    for group in (js, java):
        if group:
            group[0]["innermost"] = True
        refs += group
    for m in re.finditer(r"([\w./-]+\.(?:py|js|ts|tsx|go|java)):(\d+)", text_):
        if not any(r["file"] == m.group(1) and r["line"] == int(m.group(2)) for r in refs):
            refs.append({"file": m.group(1), "line": int(m.group(2)), "symbol": ""})
    return refs


def coverage(query: str, hit: "Hit") -> float:
    """Share of the question's meaningful words that appear in the best passage (0..1). Unlike the fused score, this
    says whether the passage is about the question at all, so it can tell "the docs do not cover this" from a match."""
    terms = set(query_terms(query))
    if not terms:
        return 0.0
    c = hit.chunk
    have = set(tokenize(c.content)) | set(tokenize(c.file_path)) | set(tokenize(c.symbol_name))
    return len(terms & have) / len(terms)


def search_chunks(
    db: Session,
    org_id: str,
    query: str,
    repo_ids: list[str] | None = None,
    k: int = 10,
    embedder: Embedder | None = None,
    languages: list[str] | None = None,
    mode: str = "hybrid",
    rerank: str | None = None,
) -> list[Hit]:
    embedder = embedder or Embedder()
    terms = query_terms(query)
    if not terms:
        return []
    qvec = embedder.embed([query])[0]
    pool = k * 6
    min_sim = get_settings().vector_min_similarity

    base = select(CodeChunk).where(CodeChunk.org_id == org_id)
    if repo_ids:
        base = base.where(CodeChunk.repo_id.in_(repo_ids))
    if languages:
        base = base.where(CodeChunk.language.in_(languages))

    if db.get_bind().dialect.name == "postgresql":
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import Float, literal

        dist = CodeChunk.embedding.op("<=>", return_type=Float)(literal(qvec, Vector(len(qvec))))
        vec_rows = db.scalars(base.where(dist <= 1 - min_sim).order_by(dist).limit(pool)).all()
        ts = " OR ".join(terms[:24])
        lex_stmt = (
            base.where(text("to_tsvector('simple', code_chunks.content) @@ websearch_to_tsquery('simple', :q)"))
            .order_by(
                text("ts_rank_cd(to_tsvector('simple', code_chunks.content), websearch_to_tsquery('simple', :q)) DESC")
            )
            .limit(pool)
            .params(q=ts)
        )
        lex_rows = db.scalars(lex_stmt).all()
        candidates = {c.id: c for c in [*vec_rows, *lex_rows]}
        vec_order = [c.id for c in vec_rows]
        lex_order = [c.id for c in lex_rows]
    else:
        all_rows = db.scalars(base).all()
        candidates = {c.id: c for c in all_rows}
        vscores = [(cosine(qvec, c.embedding), c.id) for c in all_rows if c.embedding]
        vec_order = [
            i for s, i in sorted(vscores, key=lambda x: (-x[0], _pos(candidates[x[1]])))[:pool] if s >= min_sim
        ]
        lscores = []
        for c in all_rows:
            toks = set(tokenize(c.content + " " + c.file_path + " " + c.symbol_name))
            n = sum(1 for t in terms if t in toks)
            if n:
                lscores.append((n / len(terms), c.id))
        lex_order = [i for _, i in sorted(lscores, key=lambda x: (-x[0], _pos(candidates[x[1]])))[:pool]]

    if mode == "lexical":  # keyword-only baseline (evaluation)
        vec_order = []
    elif mode == "vector":
        lex_order = []
    scores: dict[str, float] = {}
    hits: dict[str, Hit] = {}
    for order, attr in ((vec_order, "vector_rank"), (lex_order, "lexical_rank")):
        for rank, cid in enumerate(order, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            h = hits.setdefault(cid, Hit(candidates[cid], 0.0))
            setattr(h, attr, rank)
    term_set = set(terms)
    for cid, h in hits.items():
        c = h.chunk
        sym_tokens = set(tokenize(c.symbol_name))
        path_tokens = set(tokenize(c.file_path))
        h.matched_terms = sorted(term_set & (sym_tokens | path_tokens | set(tokenize(c.content))))[:8]
        if c.symbol_name and (sym_tokens & term_set):
            h.symbol_match = True
            scores[cid] += 0.5 / (RRF_K)  # exact symbol hit boost
        if term_set & path_tokens:
            scores[cid] += 0.2 / RRF_K
        h.score = scores[cid]
    provider = get_settings().rerank_provider if rerank is None else rerank
    ranked = sorted(hits.values(), key=lambda h: (-h.score, _pos(h.chunk)))
    if provider != "none" and len(ranked) > 1:
        head = ranked[: max(get_settings().rerank_depth, k)]
        new = rerank_scores(
            provider,
            query,
            terms,
            [(h.chunk.content, f"{h.chunk.file_path} {h.chunk.parent_symbol} {h.chunk.symbol_name}") for h in head],
            [h.score for h in head],
        )
        for h, s in zip(head, new, strict=True):
            h.score = s
        ranked = sorted(head, key=lambda h: (-h.score, _pos(h.chunk)))
    return ranked[:k]


def search_changes(
    db: Session,
    org_id: str,
    query: str,
    repo_ids: list[str] | None = None,
    k: int = 10,
    embedder: Embedder | None = None,
) -> list[tuple[Change, float]]:
    embedder = embedder or Embedder()
    terms = set(query_terms(query))
    if not terms:
        return []
    qvec = embedder.embed([query])[0]
    stmt = select(Change).where(Change.org_id == org_id)
    if repo_ids:
        stmt = stmt.where(Change.repo_id.in_(repo_ids))
    rows = db.scalars(stmt.order_by(Change.committed_at.desc()).limit(2000)).all()
    out = []
    min_sim = get_settings().vector_min_similarity
    now = datetime.now(UTC)
    for c in rows:
        toks = set(tokenize(c.message + " " + " ".join(f["path"] for f in c.files)))
        lex = len(terms & toks) / len(terms)
        vec = max(cosine(qvec, c.embedding), 0.0) if c.embedding else 0.0
        dt = c.committed_at if c.committed_at.tzinfo else c.committed_at.replace(tzinfo=UTC)
        age_days = max((now - dt).days, 0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        score = 0.55 * lex + 0.3 * vec + 0.15 * recency
        if lex > 0 or vec > max(0.25, min_sim):
            out.append((c, score))
    return sorted(out, key=lambda x: x[1], reverse=True)[:k]


def search_memory(
    db: Session,
    org_id: str,
    query: str,
    k: int = 3,
    embedder: Embedder | None = None,
    min_score: float = 0.25,
    scope=None,
) -> list[tuple[MemoryEntry, float]]:
    embedder = embedder or Embedder()
    qvec = embedder.embed([query])[0]
    rows = db.scalars(select(MemoryEntry).where(MemoryEntry.org_id == org_id, MemoryEntry.kind == "incident")).all()
    if scope is not None and scope.restricted:  # recall only memories derived entirely from repositories in scope
        rows = [m for m in rows if m.scope_repo_ids is not None and scope.can_all(m.scope_repo_ids)]
    scored = [(m, cosine(qvec, m.embedding)) for m in rows if m.embedding]
    return sorted([x for x in scored if x[1] >= min_score], key=lambda x: x[1], reverse=True)[:k]


def chunks_for_file(db: Session, org_id: str, repo_id: str, path: str) -> list[CodeChunk]:
    return list(
        db.scalars(
            select(CodeChunk)
            .where(CodeChunk.org_id == org_id, CodeChunk.repo_id == repo_id, CodeChunk.file_path == path)
            .order_by(CodeChunk.start_line)
        )
    )


def find_callers(db: Session, org_id: str, repo_ids: list[str], symbol: str, limit: int = 15) -> list[CodeChunk]:
    """Static call-graph lookup: chunks whose extracted `calls` include the symbol (provenance: static_analysis)."""
    if not symbol:
        return []
    stmt = select(CodeChunk).where(CodeChunk.org_id == org_id)
    if repo_ids:
        stmt = stmt.where(CodeChunk.repo_id.in_(repo_ids))
    return [c for c in db.scalars(stmt).all() if symbol in (c.calls or [])][:limit]
