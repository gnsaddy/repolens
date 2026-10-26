"""Names an answer asserts that do not exist: file paths, routes and functions the model wrote that are in neither
the sources it was given nor anywhere in the indexed repositories. No model is called. A name that exists in the
repositories but was not among this answer's sources is fine (the sources are a small slice); only a name found
nowhere is reported."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CodeChunk
from .access import Scope

MAX_NAMES = 12  # names looked up per answer
_FENCE = re.compile(r"```.*?```", re.S)  # code blocks are examples the model wrote; their names are not claims
_EXT = "py|ts|tsx|js|jsx|mjs|java|go|rb|rs|cs|kt|md|yml|yaml|json|toml|sql|sh"
_PATH = re.compile(rf"(?<![\w/.:-])((?:[\w.-]+/)*[\w-]+\.(?:{_EXT}))\b")
_ROUTE = re.compile(r"`(?:(?:GET|POST|PUT|PATCH|DELETE)\s+)?(/api/[\w{}/.:-]*|/(?:scim|auth|v\d)[\w{}/.:-]*)`")
_FUNC = re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)\(\)`")
_PARAM = re.compile(r"\{[^}]*\}")


def extract(answer: str) -> dict[str, list[str]]:
    """The paths, routes and function names the answer states, outside code blocks, each once, in order."""
    text = _FENCE.sub(" ", answer)
    return {
        "path": list(dict.fromkeys(_PATH.findall(text))),
        "route": list(dict.fromkeys(_ROUTE.findall(text))),
        "func": list(dict.fromkeys(_FUNC.findall(text))),
    }


def _norm(s: str) -> str:
    return _PARAM.sub("{}", s.lower())


def _known(results: list[dict], question: str) -> str:
    """Everything the answer was shown, flattened and lower-cased, so a name can be looked for in it."""
    parts = [question]
    for r in results:
        parts += [str(r.get("title", "")), str(r.get("file", "")), str(r.get("content", ""))]
    return _norm("\n".join(parts))


def _in_repos(db: Session, org: str, scope: Scope | None, kind: str, name: str) -> bool:
    """Whether the name exists anywhere in the repositories this person can see."""
    base = select(CodeChunk.repo_id).where(CodeChunk.org_id == org).limit(25)
    if kind == "path":
        stmt = base.where(CodeChunk.file_path.ilike(f"%{name}"))
    elif kind == "func":
        last = name.rsplit(".", 1)[-1]
        stmt = base.where((CodeChunk.symbol_name == last) | (CodeChunk.parent_symbol == last))
    else:  # a route: compare the part before the first {parameter}, which is where the names differ
        stem = name.split("{", 1)[0].rstrip("/")
        stmt = base.where(CodeChunk.chunk_type == "routes", CodeChunk.content.ilike(f"%{stem}%"))
    return any(scope is None or scope.can(rid) for rid in db.scalars(stmt))


def unsupported(
    db: Session, org: str, answer: str, results: list[dict], question: str, kind: str, scope: Scope | None
) -> list[str]:
    """Names in the answer that are in neither its sources nor the indexed repositories, as the answer wrote them.
    Build requests are skipped: writing new files and functions is what they are for."""
    if kind == "build":
        return []
    names = extract(answer)
    blob = _known(results, question)
    out: list[str] = []
    budget = MAX_NAMES
    for what in ("path", "route", "func"):
        for name in names[what]:
            if _norm(name) in blob or name.lower() in blob:
                continue
            if budget <= 0:
                break
            budget -= 1
            if not _in_repos(db, org, scope, what, name):
                out.append(f"{name}()" if what == "func" else name)
    return out
