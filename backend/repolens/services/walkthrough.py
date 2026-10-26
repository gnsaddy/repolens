"""How/why/explain questions: find the routes the question is about, the functions that handle them, and what those call.

A "how does X work" answer needs the whole path through the code, not the few passages that happen to share words with the
question. Route declarations sit outside the function chunks that get searched, so the index-time route summaries
(`services/routes.py`) are matched to the question, and each matching route brings its handler and the functions the
handler calls (a permission check, a validator, a writer)."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CodeChunk, Repository
from . import routes
from .access import Scope

EXPLAIN = re.compile(
    r"\b(how\b|why|explain|walk[- ]?through|walk me|"
    r"what happens|lifecycle|flow of|flowchart|step by step|under the hood)",
    re.I,
)
LOOKUP = re.compile(
    r"\bhow (many|much|often|long|old|big|far|fast|soon)\b", re.I
)  # a number or a name, not a walkthrough


STOP = frozenset(
    "a an the is are was were be been do does did how what why when where which who whom can could should would will "
    "i we you it this that these those to of in on for with from by and or not my our your me us as at if so about "
    "there their them they any some get got use using used please tell explain show check look".split()
)
GENERIC = frozenset(
    "list lists enumerate show give all every each complete which endpoint endpoints route routes api apis url urls "
    "flowchart explain describe please found many work works working process step steps flow code function "
    "write create add implement build generate draft develop make".split()
)
MAX_ROUTES = 3
MAX_CALLEES = 2

WALKTHROUGH = (
    " This is a how or why question, so give a thorough walkthrough, not a one-liner. Start with a two-sentence summary. "
    "Then give the steps in the order they happen (for a request: the route, input validation, the permission check, the "
    "data read or written, the response), each naming the file and function and citing [S#]. Finish with a short "
    "'Not covered by your sources' list for anything you could not find. Do not invent steps. Use Markdown."
)
MAX_TOKENS_WALKTHROUGH = 2500


BUILD = re.compile(
    r"(^|[.?!:]\s*|\b(please|can you|could you|let'?s|ok|okay|now)\W+)(write|create|add|implement|build|generate|draft|develop|make)\b",
    re.I,
)
BUILD_ADDON = (
    " The person wants something written (code, a document or a plan). Base it on the conventions, names and imports shown in "
    "the SOURCES, and use only modules, functions and settings that appear there. If something you need is not shown, say "
    "so and mark that part as an assumption instead of inventing it. Anything you write from general knowledge, not from the "
    "sources, must be labelled 'Illustrative, not from your repository'. Say first whether the sources already contain "
    "something that does what is asked, and cite it."
)
MAX_TOKENS_BUILD = 3000


def task_kind(question: str) -> str:
    """'build' (write or create something), 'explain' (how or why), or 'lookup' (find a fact)."""
    if BUILD.search(question) and not LOOKUP.search(question):
        return "build"
    return "explain" if is_explain(question) else "lookup"


def addon(kind: str) -> str:
    return {"explain": WALKTHROUGH, "build": BUILD_ADDON}.get(kind, "")


def max_tokens(kind: str, default: int) -> int:
    return {"explain": MAX_TOKENS_WALKTHROUGH, "build": MAX_TOKENS_BUILD}.get(kind, default)


def needs_path(kind: str) -> bool:
    """Questions that need the route, its handler and what it calls, not just similar text."""
    return kind in ("explain", "build")


def is_explain(question: str) -> bool:
    return bool(EXPLAIN.search(question)) and not LOOKUP.search(question)


def stem(w: str) -> str:
    """Light stemming so 'roles' meets 'role', 'users' meets 'user' and 'assigned' meets 'assign'."""
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("sses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    return w


def terms(text: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z][a-z0-9_]{2,}", text.lower()) if w not in STOP)


def _tokens(text: str) -> frozenset[str]:
    """Words in a path, a name or code, with snake_case and camelCase split, lightly stemmed."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return frozenset(stem(w) for w in re.findall(r"[A-Za-z]{3,}", spaced))


def topic(question: str) -> frozenset[str]:
    return frozenset(stem(t) for t in terms(question) if t not in GENERIC) - frozenset(stem(g) for g in GENERIC)


def key_words(question: str) -> frozenset[str]:
    """The question's content words in the same form `_tokens` gives text (split on underscores, lightly stemmed), so the two
    can be compared: what the sources must mention for an answer to be built from them."""
    kept = " ".join(t for t in terms(question) if t not in GENERIC)
    return _tokens(kept.replace("_", " ")) - frozenset(stem(g) for g in GENERIC)


def _result(c: CodeChunk, repo: str, title: str, via: str, content: str | None = None, line: int | None = None) -> dict:
    return {
        "chunk_id": c.id,
        "repo_id": c.repo_id,
        "repo": repo,
        "file": c.file_path,
        "kind": "code",
        "chunk_type": c.chunk_type,
        "title": title,
        "symbol": c.symbol_name,
        "parent": c.parent_symbol,
        "start_line": line or c.start_line,
        "end_line": line or c.end_line,
        "content": content if content is not None else c.content,
        "score": 1.0,
        "matched_terms": [],
        "symbol_match": True,
        "node_id": None,
        "url": None,
        "connections": [],
        "via_graph": via,
    }


def route_sources(db: Session, org: str, question: str, repo_ids: list[str] | None, scope: Scope | None) -> list[dict]:
    """The routes this question is about, each followed by its handler and what the handler calls."""
    want = topic(question)
    if not want:
        return []
    stmt = select(CodeChunk).where(CodeChunk.org_id == org, CodeChunk.chunk_type == "routes")
    if repo_ids:
        stmt = stmt.where(CodeChunk.repo_id.in_(repo_ids))
    cands = []
    for c in db.scalars(stmt):
        if scope is not None and not scope.can(c.repo_id):
            continue
        for r in routes.parse(c.content):
            hits = len(want & _tokens(f"{r.path} {r.handler} {c.file_path}"))
            if hits:
                cands.append((hits, r, c))
    if not cands:
        return []
    cands.sort(key=lambda x: -x[0])
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == org))}
    # Score every candidate by what its handler does, not only by its path: in a large API dozens of routes share a word
    # like "user", and the one that answers the question is found by its body ("role"), not by being first in the list.
    wanted = {r.handler for _h, r, _c in cands if r.handler}
    handlers: dict[tuple[str, str, str], CodeChunk] = {}
    if wanted:
        for row in db.scalars(
            select(CodeChunk).where(
                CodeChunk.repo_id.in_({c.repo_id for _h, _r, c in cands}),
                CodeChunk.file_path.in_({c.file_path for _h, _r, c in cands}),
                CodeChunk.symbol_name.in_(wanted),
                CodeChunk.chunk_type != "routes",
            )
        ):
            handlers.setdefault((row.repo_id, row.file_path, row.symbol_name), row)
    scored = []
    for hits, r, c in cands:
        h = handlers.get((c.repo_id, c.file_path, r.handler)) if r.handler else None
        body = len(want & _tokens(h.content)) if h else 0
        scored.append((2 * hits + body, _mentions(want, h.content) if h else 0, r, c, h))
    # Equal scores are common (several routes share the word and one body word), so the handler that mentions the topic
    # most often goes first, and the top slots are spread over files so one module of near-identical routes cannot fill them.
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out: list[dict] = []
    seen: set[str] = set()
    for _score, _mentioned, r, c, h in _spread(scored, MAX_ROUTES):
        repo = names.get(c.repo_id, "")
        out.append(
            _result(
                c,
                repo,
                f"{r.method} {r.path}",
                "route",
                f"HTTP route: {r.method} {r.path} is handled by {r.handler or 'a handler'}() in {c.file_path} "
                f"(declared at line {r.line}).",
                r.line,
            )
        )
        if h is None or h.id in seen:
            continue
        seen.add(h.id)
        out.append(_result(h, repo, f"{h.symbol_name} (handler for {r.method} {r.path})", "handler"))
        out += _callees(db, h, repo, want, seen)
    return out


def _mentions(want: frozenset[str], text: str) -> int:
    """How many times the text uses any of the wanted words (same splitting and stemming as `_tokens`)."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return sum(1 for w in re.findall(r"[A-Za-z]{3,}", spaced) if stem(w) in want)


def _spread(scored: list, limit: int) -> list:
    """The best `limit` entries, taking at most one per file first and then the best of the rest, in score order."""
    picked: list[int] = []
    files: set[tuple[str, str]] = set()
    for i, item in enumerate(scored):
        key = (item[3].repo_id, item[3].file_path)
        if key not in files:
            files.add(key)
            picked.append(i)
        if len(picked) >= limit:
            break
    for i in range(len(scored)):
        if len(picked) >= limit:
            break
        if i not in picked:
            picked.append(i)
    return [scored[i] for i in sorted(picked)]


def _callees(db: Session, handler: CodeChunk, repo: str, want: frozenset[str], seen: set[str]) -> list[dict]:
    names = [n for n in dict.fromkeys(handler.calls or []) if n and n != handler.symbol_name]
    if not names:
        return []
    rows = db.scalars(
        select(CodeChunk).where(
            CodeChunk.repo_id == handler.repo_id,
            CodeChunk.symbol_name.in_(names),
            CodeChunk.chunk_type != "routes",
            CodeChunk.id != handler.id,
        )
    ).all()
    checks = re.compile(r"authoriz|permission|polic|require|check|valid|verify|guard", re.I)

    def rank(c: CodeChunk) -> tuple[int, int]:
        return (0 if checks.search(c.symbol_name) else 1, -len(want & _tokens(c.content)))

    out: list[dict] = []
    for c in sorted(rows, key=rank):
        if c.id in seen or len(out) >= MAX_CALLEES:
            continue
        seen.add(c.id)
        out.append(_result(c, repo, f"{c.symbol_name} (called by {handler.symbol_name})", "calls"))
    return out


def merge(results: list[dict], extra: list[dict], k: int) -> list[dict]:
    """The route path first, then the ordinary search results without repeats."""
    have = {r["chunk_id"] for r in extra}
    return extra + [r for r in results if r.get("chunk_id") not in have][: k + 3]
