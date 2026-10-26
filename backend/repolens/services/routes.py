"""HTTP routes found in source files, kept as one searchable summary chunk per file.

Route decorators sit outside the function a code chunk covers, and a question like "list all the API endpoints" needs every
route, not the eight best matches. So at index time each file's routes are written down once (method, path, handler, line)
and a list-everything question can read them all. Pattern based, so it is a good inventory rather than a guarantee: routes
registered dynamically, or with unusual wrappers, are not seen."""

import re
from dataclasses import dataclass

from .parsing import ParsedChunk

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
CODE_SUFFIX = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".java", ".go", ".kt")
RECEIVERS = {"app", "router", "routes", "server", "fastify", "api_router", "r", "e", "g", "group", "mux"}

PY_DECORATOR = re.compile(rf"^\s*@(\w+)\.({'|'.join(METHODS)})\(\s*(?:path\s*=\s*)?[\"']([^\"']*)[\"']")
PY_ROUTE = re.compile(r"^\s*@(\w+)\.route\(\s*[\"']([^\"']*)[\"'](.*)")
PY_PREFIX = re.compile(r"(?:APIRouter|Blueprint)\(.*?(?:url_)?prefix\s*=\s*[\"']([^\"']*)[\"']")
PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
JS_ROUTE = re.compile(rf"\b(\w+)\.({'|'.join(METHODS)})\(\s*[\"'`](/[^\"'`]*)[\"'`]")
SPRING = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*\"([^\"]*)\"")
SPRING_FN = re.compile(r"(\w+)\s*\(")
GO_ROUTE = re.compile(r"\.(GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc)\(\s*\"(/[^\"]*)\"")
TESTISH = re.compile(r"(^|/)(tests?|__tests__|e2e|spec)(/|$)|\.test\.|\.spec\.|(^|/)test_|_test\.")


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: str
    line: int


def _join(prefix: str, path: str) -> str:
    return "/" + "/".join(x for x in (prefix.strip("/"), path.strip("/")) if x) if (prefix or path) else "/"


def extract(path: str, text: str) -> list[Route]:
    if not path.endswith(CODE_SUFFIX) or TESTISH.search(path) or len(text) > 600_000:
        return []
    lines = text.splitlines()
    prefix = ""
    out: list[Route] = []

    def handler_after(i: int, pattern: re.Pattern) -> str:
        for ln in lines[i + 1 : i + 8]:
            if ln.lstrip().startswith("@"):
                continue
            m = pattern.search(ln)
            if m:
                return m.group(1)
        return ""

    if path.endswith(".py"):
        m = PY_PREFIX.search(text)
        prefix = m.group(1) if m else ""
    for i, ln in enumerate(lines):
        if path.endswith(".py"):
            if m := PY_DECORATOR.match(ln):
                out.append(Route(m.group(2).upper(), _join(prefix, m.group(3)), handler_after(i, PY_DEF), i + 1))
            elif m := PY_ROUTE.match(ln):
                found = re.findall(r"[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", m.group(3), re.I) or ["GET"]
                out += [Route(x.upper(), _join(prefix, m.group(2)), handler_after(i, PY_DEF), i + 1) for x in found]
        elif path.endswith((".java", ".kt")):
            if m := SPRING.search(ln):
                verb = "ANY" if m.group(1) == "Request" else m.group(1).upper()
                out.append(Route(verb, _join("", m.group(2)), handler_after(i, SPRING_FN), i + 1))
        elif path.endswith(".go"):
            if m := GO_ROUTE.search(ln):
                verb = "ANY" if m.group(1).startswith("Handle") else m.group(1)
                out.append(Route(verb, m.group(2), "", i + 1))
        elif (m := JS_ROUTE.search(ln)) and m.group(1) in RECEIVERS:
            out.append(Route(m.group(2).upper(), m.group(3), "", i + 1))
    return out


def chunks(path: str, text: str) -> list[ParsedChunk]:
    found = extract(path, text)
    if not found:
        return []
    body = "\n".join(
        f"{r.method} {r.path}" + (f" -> {r.handler}" if r.handler else "") + f" (line {r.line})" for r in found
    )
    return [
        ParsedChunk(
            symbol="API routes",
            parent="",
            chunk_type="routes",
            start_line=found[0].line,
            end_line=found[-1].line,
            content=f"API endpoints (routes) defined in {path}:\n{body}",
        )
    ]


LINE = re.compile(r"^([A-Z]+) (\S+)(?: -> (\w+))? \(line (\d+)\)$")


def parse(content: str) -> list[Route]:
    """The routes written by `chunks`, read back."""
    out = []
    for ln in content.splitlines()[1:]:
        m = LINE.match(ln.strip())
        if m:
            out.append(Route(m.group(1), m.group(2), m.group(3) or "", int(m.group(4))))
    return out
