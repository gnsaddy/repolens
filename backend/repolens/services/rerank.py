"""Second-stage reranking of the fused retrieval candidates.

Hybrid search (vectors + keywords, fused with RRF) is good at *recall*; a reranker re-reads each candidate together
with the query to improve *precision* in the top few results. Two implementations:

* ``lexical``: offline. BM25 over the candidate set, query-term proximity inside the chunk, and exact identifier hits.
  No model, no network, deterministic.
* ``api``: any Cohere / Jina / Voyage style ``POST {base}/rerank`` endpoint (a cross-encoder or hosted reranker).
  Falls back to the fused order if the service fails, so retrieval never breaks because a reranker is down.
"""

import logging
import math
import re

import httpx

from ..config import get_settings
from .embeddings import tokenize

log = logging.getLogger("repolens.rerank")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _norm(xs: list[float]) -> list[float]:
    lo, hi = min(xs), max(xs)
    return [0.5] * len(xs) if hi - lo < 1e-12 else [(x - lo) / (hi - lo) for x in xs]


def _proximity(tokens: list[str], terms: list[str]) -> float:
    """1.0 when every matched query term sits next to the others, falling towards 0 as they spread out."""
    present = [t for t in dict.fromkeys(terms) if t in tokens]
    if len(present) < 2:
        return 0.0
    events = sorted((i, w) for i, w in enumerate(tokens) if w in present)
    need, left, best = len(present), 0, len(tokens)
    have: dict[str, int] = {}
    for pos, term in events:
        have[term] = have.get(term, 0) + 1
        while len(have) == need:
            best = min(best, pos - events[left][0] + 1)
            lt = events[left][1]
            have[lt] -= 1
            if not have[lt]:
                del have[lt]
            left += 1
    return min(1.0, need / best) * (need / max(len(terms), 1))


def lexical_scores(query_terms: list[str], docs: list[tuple[str, str]]) -> list[float]:
    """Score (text, title) pairs against the query terms. BM25 + proximity + identifier match, each in [0, 1]."""
    toks = [tokenize(f"{title} {title} {text}") for text, title in docs]
    n = len(docs)
    avg = (sum(len(t) for t in toks) / n) or 1.0
    df = {q: sum(1 for t in toks if q in t) for q in query_terms}
    bm25, prox, ident = [], [], []
    for (_, title), t in zip(docs, toks, strict=True):
        tf = {q: t.count(q) for q in query_terms}
        s = 0.0
        for q in query_terms:
            if not tf[q]:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * tf[q] * 2.2 / (tf[q] + 1.2 * (0.25 + 0.75 * len(t) / avg))
        bm25.append(s)
        prox.append(_proximity(t, query_terms))
        names = {w.lower() for w in IDENT.findall(title)} | {part for w in IDENT.findall(title) for part in tokenize(w)}
        ident.append(sum(1 for q in query_terms if q in names) / max(len(query_terms), 1))
    b = _norm(bm25)
    return [0.6 * b[i] + 0.25 * prox[i] + 0.15 * ident[i] for i in range(n)]


def api_scores(query: str, docs: list[str]) -> list[float] | None:
    s = get_settings()
    if not s.rerank_base_url:
        return None
    try:
        r = httpx.post(
            f"{s.rerank_base_url.rstrip('/')}/rerank",
            headers={"Authorization": f"Bearer {s.rerank_api_key}"},
            json={"model": s.rerank_model, "query": query, "documents": [d[:2000] for d in docs], "top_n": len(docs)},
            timeout=s.rerank_timeout_s,
        )
        r.raise_for_status()
        out = [0.0] * len(docs)
        for item in r.json().get("results") or r.json().get("data") or []:
            out[int(item["index"])] = float(item.get("relevance_score", item.get("score", 0.0)))
        return out
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
        log.warning("rerank service failed (%s); keeping the fused order", e)
        return None


def rerank(provider: str, query: str, terms: list[str], docs: list[tuple[str, str]], fused: list[float]) -> list[float]:
    """Return a new score per candidate (higher is better). The fused score is blended in so strong first-stage
    evidence is never thrown away by a weak second-stage opinion."""
    if provider == "api":
        second = api_scores(query, [f"{t}\n{x}" for x, t in docs])
        if second is None:
            return fused
        w = 0.7
    elif provider == "lexical":
        second, w = lexical_scores(terms, docs), 0.5
    else:
        return fused
    a, b = _norm(fused), _norm(second)
    return [(1 - w) * a[i] + w * b[i] for i in range(len(fused))]
