"""Shrink retrieved context before it reaches a model. Tokens are the cost, so send the lines that matter."""

import hashlib
import re

from . import embeddings

_STOP = frozenset(
    (
        "a an the of to in on for and or is are was were be do does how what why when where which who can with by at "
        "from as it this that"
    ).split()
)


def _terms(query: str) -> set[str]:
    return {embeddings.stem(t.lower()) for t in embeddings.tokenize(query) if t.lower() not in _STOP and len(t) > 2}


def condense(text: str, terms: set[str], budget: int) -> str:
    """The first line (usually the heading) plus lines that mention the query's terms, within `budget` characters."""
    if len(text) <= budget:
        return text
    lines = text.splitlines()
    scored = []
    for i, ln in enumerate(lines):
        hit = len(terms & {embeddings.stem(t.lower()) for t in embeddings.tokenize(ln)})
        scored.append((hit, i))
    keep = {0}
    for hit, i in sorted(scored, key=lambda t: (-t[0], t[1])):
        if hit == 0:
            break
        keep.update({i, i + 1} if i + 1 < len(lines) else {i})  # a matching line and the one after it
    if len(keep) == 1:  # nothing matched: fall back to the start of the text
        return text[:budget]
    out: list[str] = []
    used = 0
    for i in sorted(keep):
        if i >= len(lines):
            continue
        ln = lines[i][:400]
        if used + len(ln) > budget:
            break
        if out and i > 1 and (i - 1) not in keep:
            out.append("…")
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def blocks(sources: list[dict], results: list[dict], query: str, budget: int) -> list[tuple[dict, str]]:
    """(source, text) pairs for the prompt: repeated passages are dropped and the rest are condensed to the budget."""
    seen: set[str] = set()
    keep = []
    for s, r in zip(sources, results, strict=True):
        key = hashlib.sha256(re.sub(r"\s+", " ", r["content"]).strip().lower().encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            keep.append((s, r))
    terms = _terms(query)
    per = max(400, budget // max(len(keep), 1))
    return [(s, condense(r["content"], terms, per)) for s, r in keep]
