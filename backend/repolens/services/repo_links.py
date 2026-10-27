"""Dependencies between repositories: found by independent detectors, kept with when they first appeared.

A detector looks at the indexed repositories of a workspace and reports links, each from a function, class or file in
one repository to one in another, with the text that proves it. Today's detectors read imports (Python, JavaScript
and TypeScript) and match URLs against the routes another repository serves. The links are stored, so a page does not
search the code again each time it is opened, and so the store can say what is new since last week. Each detector also
reports what it could and could not read, because an empty result must never be mistaken for "no dependencies"."""

import hashlib
import json
import logging
import posixpath
import re
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Change, CodeChunk, KGEdge, KGNode, LinkState, RepoLink, Repository
from . import routes as routes_svc
from .access import Scope

log = logging.getLogger("repolens.links")

HTTP_WEIGHT = 0.7
MAX_HTTP = 400
GENERIC_PATHS = {"/health", "/healthz", "/ready", "/readyz", "/metrics", "/login", "/logout", "/status", "/ping"}
GENERIC_PATHS |= {"/api", "/version", "/docs", "/favicon.ico", "/robots.txt"}
_QUOTED = re.compile(r"""(["'`])((?:(?!\1)[^\n\\]){3,240})\1""")
_PATHRUN = re.compile(r"/[\w\-.~:{}$<>]+(?:/[\w\-.~:{}$<>]+)*")
_PARAM = re.compile(r"\$\{[^}]*\}|\{[^}]*\}|<[^>]*>|:\w+")
_EXAMPLE = re.compile(
    r"(^|/)(docs?|docs_src|examples?|samples?|tutorials?|fixtures?|vendor|third_party|scripts)(/|$)", re.I
)


def _norm(path: str) -> str:
    return _PARAM.sub("{}", path.split("?")[0].split("#")[0].rstrip("/")).lower()


def _specific(path: str) -> bool:
    """A route worth matching: at least two fixed words (`/pricing/quote/{}`), or one long one. `/users/{}` and
    `/runs/{}` are in every project, so a URL that merely looks like them says nothing; neither does a health check."""
    segs = [s for s in path.split("/") if s]
    fixed = [s for s in segs if "{" not in s and len(s) >= 3]  # `/users/me` is in every identity API
    return path not in GENERIC_PATHS and (len(fixed) >= 2 or (len(segs) == 1 and bool(fixed) and len(fixed[0]) >= 8))


def _http_links(db: Session, ids: list[str]) -> list[dict]:
    """Places where one repository writes a URL path that another serves: `payments` building `/users/{id}` while
    `users` declares `GET /users/{user_id}`. Read from the stored route inventory and the code that was indexed, so
    nothing is re-indexed. Pattern based: a URL assembled from pieces, or called through a name, is missed."""
    if len(ids) < 2:
        return []
    served: dict[str, list[tuple[str, str, routes_svc.Route]]] = defaultdict(
        list
    )  # normalised path -> where it is served
    for repo_id, file, content in db.execute(
        select(CodeChunk.repo_id, CodeChunk.file_path, CodeChunk.content).where(
            CodeChunk.repo_id.in_(ids), CodeChunk.chunk_type == "routes"
        )
    ):
        if routes_svc.TESTISH.search(file) or _EXAMPLE.search(file):
            continue
        for r in routes_svc.parse(content):
            segs = _norm(r.path).split("/")[1:]
            for i in range(len(segs)):  # also by its tail, since a caller may leave out a gateway prefix such as /api
                tail = "/" + "/".join(segs[i:])
                if _specific(tail) and (i == 0 or len(segs) - i >= 2):
                    served[tail].append((repo_id, file, r))
    found: list[dict] = []
    if served:
        seen: set[tuple[str, str]] = set()
        rows = db.execute(
            select(CodeChunk.id, CodeChunk.repo_id, CodeChunk.file_path, CodeChunk.symbol_name, CodeChunk.content)
            .where(CodeChunk.repo_id.in_(ids), CodeChunk.chunk_type.in_(("function", "method", "class")))
            .execution_options(yield_per=500)
        )
        callers: list[tuple[str, str, str, str, str, tuple[str, str, routes_svc.Route]]] = []
        for cid, repo_id, file, sym, content in rows:
            if "/" not in content or routes_svc.TESTISH.search(file) or _EXAMPLE.search(file):
                continue
            for _, lit in _QUOTED.findall(content):
                for run in _PATHRUN.findall(lit):
                    segs = _norm(run).split("/")[1:]
                    for i in range(len(segs)):  # the literal may start with a base URL: try each shorter tail
                        hit = next((s for s in served.get("/" + "/".join(segs[i:]), []) if s[0] != repo_id), None)
                        if hit and (cid, hit[1] + hit[2].path) not in seen:
                            seen.add((cid, hit[1] + hit[2].path))
                            callers.append((cid, repo_id, file, sym, run, hit))
                            break
            if len(callers) >= MAX_HTTP:
                break
        node_rows = db.execute(select(KGNode.chunk_id, KGNode.id).where(KGNode.chunk_id.in_([c[0] for c in callers])))
        by_chunk = {chunk: node for chunk, node in node_rows}
        for cid, repo_id, file, sym, run, (dst_repo, dst_file, route) in callers:
            target = db.scalar(
                select(KGNode.id)
                .where(KGNode.repo_id == dst_repo, KGNode.path == dst_file, KGNode.name == route.handler)
                .limit(1)
            ) or db.scalar(
                select(KGNode.id).where(KGNode.repo_id == dst_repo, KGNode.path == dst_file, KGNode.type == "file")
            )
            if cid in by_chunk and target:
                found.append(
                    {
                        "src": by_chunk[cid],
                        "dst": target,
                        "src_repo": repo_id,
                        "dst_repo": dst_repo,
                        "from": f"{file}:{sym}",
                        "to": f"{dst_file}:{route.handler or route.path}",
                        "evidence": f"{sym} uses the URL {run}, which {route.method} {route.path} serves",
                        "weight": HTTP_WEIGHT,
                    }
                )
    return found


# --- dependencies proved by imports ------------------------------------------------------------------------------

IMPORT_LANGS = ("python", "javascript", "typescript", "tsx")
MAX_IMPORT_EDGES = 600
CALLERS_SHOWN = 4  # functions drawn per imported class or function; the rest are counted
_PY_FROM = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$", re.S)
_PY_IMPORT = re.compile(r"^\s*import\s+(.+)$", re.S)
_JS_IMPORT = re.compile(r"""^\s*import\s+(?:type\s+)?(?P<what>.+?)\s+from\s+['"](?P<spec>[^'"]+)['"]""", re.S)
_REEXPORT = re.compile(r"from\s+(\.+)([\w.]*)\s+import\s+(\([^)]*\)|[^\n]+)")


def _names(text: str) -> list[tuple[str, str]]:
    """`a, b as c` -> [(a, a), (b, c)]: what is imported and the name it has in the file."""
    text = re.sub(r"#[^\n]*", "", text).strip().strip("()")
    out = []
    for part in text.split(","):
        name, _, alias = part.strip().partition(" as ")
        if name.strip() and name.strip() != "*":
            out.append((name.strip(), (alias or name).strip()))
    return out


def parse_imports(stmts: Sequence[str], lang: str) -> list[dict]:
    """The imports of a file that name a package (relative imports stay inside the repository and are skipped):
    {top, module, names: [(name, alias)], whole: alias or None, stmt}."""
    out = []
    for stmt in stmts or []:
        if lang == "python":
            m = _PY_FROM.match(stmt)
            if m:
                if not m.group(1).startswith("."):
                    top, _, rest = m.group(1).partition(".")
                    out.append({"top": top, "module": rest, "names": _names(m.group(2)), "whole": None, "stmt": stmt})
                continue
            m = _PY_IMPORT.match(stmt)
            for part in m.group(1).split(",") if m else []:
                mod, _, alias = part.strip().partition(" as ")
                top, _, rest = mod.strip().partition(".")
                if top.isidentifier():
                    out.append({"top": top, "module": rest, "names": [], "whole": (alias or top).strip(), "stmt": stmt})
        else:
            m = _JS_IMPORT.match(stmt)
            if not m or m.group("spec").startswith("."):
                continue
            parts = m.group("spec").split("/")
            top = "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]
            what = m.group("what")
            braces = re.search(r"\{([^}]*)\}", what)
            rest = re.sub(r"\{[^}]*\}", "", what).replace(",", " ").replace("* as", " ").split()
            named = []
            for chunk in braces.group(1).split(",") if braces else []:
                name, _, alias = chunk.strip().partition(" as ")
                if name.strip():
                    named.append((name.strip(), (alias or name).strip()))
            module = "/".join(parts[len(top.split("/")) :])
            out.append(
                {"top": top, "module": module, "names": named, "whole": rest[-1] if rest else None, "stmt": stmt}
            )
    return out


def _packages(db: Session, ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Package name -> [(repository, directory)] for what each repository offers to others: the top directories of its
    Python packages, and the `name` of each package.json."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    dirs: dict[str, set[str]] = defaultdict(set)
    for repo_id, path in db.execute(
        select(KGNode.repo_id, KGNode.path).where(
            KGNode.repo_id.in_(ids), KGNode.type == "file", KGNode.path.like("%__init__.py")
        )
    ):
        dirs[repo_id].add(posixpath.dirname(path))
    for repo_id, found in dirs.items():
        for d in sorted(found):
            name = posixpath.basename(d)
            skip = _EXAMPLE.search(d) or routes_svc.TESTISH.search(d + "/")
            if name.isidentifier() and posixpath.dirname(d) not in found and not skip:
                out[name].append((repo_id, d))
    for repo_id, path, content in db.execute(
        select(CodeChunk.repo_id, CodeChunk.file_path, CodeChunk.content).where(
            CodeChunk.repo_id.in_(ids),
            CodeChunk.file_path.like("%package.json"),
            CodeChunk.file_path.notlike("%node_modules%"),
        )
    ):
        try:
            name = json.loads(content).get("name", "")
        except (ValueError, AttributeError):
            continue
        if isinstance(name, str) and name and not _EXAMPLE.search(path):
            out[name].append((repo_id, posixpath.dirname(path)))
    return out


class _Resolver:
    """Finds the class or function an import names inside another repository's package, following `from .x import Y`
    re-exports (the usual way a package offers its names) to where it is defined."""

    def __init__(self, db: Session):
        self.db = db
        self._text: dict[tuple[str, str], str] = {}
        self._memo: dict[tuple, tuple[str, str] | None] = {}

    def text(self, repo: str, path: str) -> str:
        if (repo, path) not in self._text:
            rows = self.db.execute(
                select(CodeChunk.content, CodeChunk.imports)
                .where(CodeChunk.repo_id == repo, CodeChunk.file_path == path)
                .order_by(CodeChunk.start_line)
            ).all()
            # a file's own import lines are kept on its definitions (a file with none keeps its text as it is)
            imports: list[str] = next((r.imports for r in rows if r.imports), [])
            self._text[(repo, path)] = "\n".join([*imports, *(r.content for r in rows)])
        return self._text[(repo, path)]

    @staticmethod
    def _from_elsewhere(text: str, name: str) -> bool:
        """Whether a file takes `name` from another package, as in `from starlette.responses import Response`."""
        for m in re.finditer(r"^from\s+(?!\.)[\w.]+\s+import\s+(\([^)]*\)|[^\n]*)", text, re.M):
            if any(alias == name for _, alias in _names(m.group(1))):
                return True
        return False

    def _defined(self, repo: str, files: list[str], name: str) -> str | None:
        return self.db.scalar(
            select(KGNode.id)
            .where(
                KGNode.repo_id == repo,
                KGNode.type.in_(("function", "class")),
                KGNode.name == name,
                KGNode.qualname == name,
                KGNode.path.in_(files),
            )
            .order_by(KGNode.path)
            .limit(1)
        )

    def _through(self, repo: str, files: list[str], name: str, depth: int) -> str | None:
        """The node defining `name`; "" when the package only passes it on from another package; None when unknown."""
        found = self._defined(repo, files, name)
        if found or depth >= 3:
            return found
        for f in files:
            text = self.text(repo, f)
            if self._from_elsewhere(text, name):
                return ""
            for dots, mod, names in _REEXPORT.findall(text):
                original = next((n for n, a in _names(names) if a == name), None)
                if original is None:
                    continue
                base = posixpath.dirname(f)
                for _ in range(len(dots) - 1):
                    base = posixpath.dirname(base)
                target = posixpath.join(base, mod.replace(".", "/")) if mod else base
                hit = self._through(repo, [target + ".py", target + "/__init__.py"], original, depth + 1)
                if hit is not None:
                    return hit
        return None

    def symbol(self, repo: str, root: str, module: str, name: str) -> tuple[str, str] | None:
        """(node id, trust): `fact` when the package's own files lead to the definition, `inferred` when the name was
        only found somewhere under the package."""
        key = (repo, root, module, name)
        if key not in self._memo:
            at = posixpath.join(root, module.replace(".", "/")) if module else root
            files = [at + ".py", at + "/__init__.py"] if module else [root + "/__init__.py"]
            hit = self._through(repo, files, name, 0)
            if hit == "":
                self._memo[key] = None  # the package passes on a name from another package; it is not defined here
                return None
            result: tuple[str, str] | None = (hit, "fact") if hit else None
            if not result:
                rows = self.db.execute(
                    select(KGNode.id, KGNode.path).where(
                        KGNode.repo_id == repo,
                        KGNode.type.in_(("function", "class")),
                        KGNode.name == name,
                        KGNode.qualname == name,
                        KGNode.path.like(root + "/%"),
                    )
                ).all()
                rows = [r for r in rows if not (routes_svc.TESTISH.search(r.path) or _EXAMPLE.search(r.path))]
                if rows:
                    result = (min(rows, key=lambda r: (len(r.path), r.path)).id, "inferred")
            self._memo[key] = result
        return self._memo[key]


def _import_links(db: Session, ids: list[str]) -> list[dict]:
    """Where one repository imports a package another repository provides and uses what it imported: `shop` writing
    `from userlib import get_user` and calling `get_user()` in `checkout()`. Each edge runs from the function that uses
    it (or the file, when only the import is there) to the class or function defined in the other repository, with the
    import line as the proof. Python and JavaScript/TypeScript imports are read; other languages are not."""
    if len(ids) < 2:
        return []
    pkgs = _packages(db, ids)
    return _scan_imports(db, ids, pkgs)[:MAX_IMPORT_EDGES] if pkgs else []


def _scan_imports(db: Session, ids: list[str], pkgs: dict[str, list[tuple[str, str]]]) -> list[dict]:
    resolver = _Resolver(db)
    offered = {r: {n for n, where in pkgs.items() if any(w[0] == r for w in where)} for r in ids}
    rows = db.execute(
        select(
            CodeChunk.id,
            CodeChunk.repo_id,
            CodeChunk.file_path,
            CodeChunk.symbol_name,
            CodeChunk.parent_symbol,
            CodeChunk.language,
            CodeChunk.imports,
            CodeChunk.calls,
        )
        .where(
            CodeChunk.repo_id.in_(ids),
            CodeChunk.language.in_(IMPORT_LANGS),
            CodeChunk.chunk_type.in_(("function", "method", "class")),
        )
        .execution_options(yield_per=500)
    )
    per_file: dict[tuple[str, str], list[dict]] = {}
    uses: dict[tuple[str, str], dict] = {}  # (chunk, symbol) -> where a function uses an imported name
    named_in: dict[tuple[str, str, str], dict] = {}  # (repository, file, symbol) -> the import that names it
    for cid, repo_id, file, sym, parent, lang, stmts, calls in rows:
        fkey = (repo_id, file)
        if fkey not in per_file:
            skip = _EXAMPLE.search(file) or routes_svc.TESTISH.search(file)
            per_file[fkey] = (
                []
                if skip
                else [
                    i
                    for i in parse_imports(stmts, lang)
                    if i["top"] not in offered[repo_id] and any(r != repo_id for r, _ in pkgs.get(i["top"], []))
                ]
            )
        called = set(calls or [])
        for imp in per_file[fkey]:
            for b_repo, root in pkgs[imp["top"]]:
                if b_repo == repo_id:
                    continue
                hits: list[tuple[str, str, str]] = []  # (name, symbol id, trust)
                for name, alias in imp["names"]:
                    r = resolver.symbol(b_repo, root, imp["module"], name)
                    if r:
                        named_in.setdefault((repo_id, file, r[0]), imp)
                        if alias in called or name in called:
                            hits.append((name, r[0], r[1]))
                if imp["whole"]:  # `import userlib` then `userlib.get_user()`: the call names what it uses
                    for name in called:
                        r = resolver.symbol(b_repo, root, imp["module"], name)
                        if r:
                            hits.append((name, r[0], "inferred"))
                for name, dst, trust in hits:
                    uses.setdefault(
                        (cid, dst),
                        {
                            "chunk": cid,
                            "dst": dst,
                            "src_repo": repo_id,
                            "dst_repo": b_repo,
                            "file": file,
                            "sym": f"{parent}.{sym}" if parent else sym,
                            "name": name,
                            "stmt": " ".join(imp["stmt"].split()),
                            "trust": trust,
                        },
                    )
        if len(uses) >= 5000:
            break
    chunk_ids = list({u["chunk"] for u in uses.values()})
    nodes: dict[str, str] = {}
    for i in range(0, len(chunk_ids), 400):
        nodes.update(
            {
                c: n
                for c, n in db.execute(
                    select(KGNode.chunk_id, KGNode.id).where(KGNode.chunk_id.in_(chunk_ids[i : i + 400]))
                )
                if c
            }
        )
    by_dst: dict[str, list[dict]] = defaultdict(list)
    for u in uses.values():
        if u["chunk"] in nodes:
            by_dst[u["dst"]].append(u)
    symbols = nodes_by_id(db, set(by_dst) | {k[2] for k in named_in})
    found: list[dict] = []
    for dst, lst in by_dst.items():
        lst.sort(key=lambda u: (u["file"], u["sym"]))
        target = symbols.get(dst)
        for n, u in enumerate(lst[:CALLERS_SHOWN]):
            more = max(0, len(lst) - CALLERS_SHOWN) if n == 0 else 0
            found.append(
                {
                    "rel": "USES",
                    "src": nodes[u["chunk"]],
                    "dst": dst,
                    "src_repo": u["src_repo"],
                    "dst_repo": u["dst_repo"],
                    "from": f"{u['file']}:{u['sym']}",
                    "to": f"{target.path}:{target.name}" if target else dst,
                    "evidence": f"{u['sym']} calls {u['name']}, imported with `{u['stmt']}`"
                    + (f" (and {more} more functions)" if more else ""),
                    "weight": 1.0 if u["trust"] == "fact" else 0.85,
                    "trust": u["trust"],
                    "more": more,
                }
            )
    # an import that no function calls (a type, a constant, a base class): the file itself depends on it
    used_files = {(u["src_repo"], u["file"], u["dst"]) for lst in by_dst.values() for u in lst}
    wanted = {(k[0], k[1]) for k in named_in if k not in used_files}
    file_nodes = {
        (r, p): i
        for i, r, p in db.execute(
            select(KGNode.id, KGNode.repo_id, KGNode.path).where(KGNode.repo_id.in_(ids), KGNode.type == "file")
        )
        if (r, p) in wanted
    }
    for (a_repo, file, dst), imp in named_in.items():
        target = symbols.get(dst)
        if (a_repo, file, dst) in used_files or (a_repo, file) not in file_nodes or target is None:
            continue
        found.append(
            {
                "rel": "IMPORTS",
                "src": file_nodes[(a_repo, file)],
                "dst": dst,
                "src_repo": a_repo,
                "dst_repo": target.repo_id,
                "from": file,
                "to": f"{target.path}:{target.name}",
                "evidence": f"{file} imports {target.name}: `{' '.join(imp['stmt'].split())}`",
                "weight": 1.0,
                "trust": "fact",
                "more": 0,
            }
        )
    return found


def nodes_by_id(db: Session, ids: set[str]) -> dict[str, KGNode]:
    out: dict[str, KGNode] = {}
    wanted = list(ids)
    for i in range(0, len(wanted), 400):
        out.update({n.id: n for n in db.scalars(select(KGNode).where(KGNode.id.in_(wanted[i : i + 400])))})
    return out


# --- detectors -----------------------------------------------------------------------------------------------------

CODE_EXT = {  # by file extension, because the parser files most languages under "text"
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".rs": "rust",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
}
# Change this whenever a detector finds links differently or coverage changes shape: stored links are then worked out
# again even though no repository was indexed (the same idea as `answer_memory.PIPELINE`).
DETECTION_VERSION = "d2"
KEEP_GONE_DAYS = 180  # a link that disappeared stays listed as removed this long
SYMBOLS = ("function", "method", "class")


@dataclass(frozen=True)
class Link:
    kind: str  # which detector found it: import | http
    rel: str  # USES | IMPORTS | HTTP
    tier: str  # imported: a file names it; used: a function calls it
    trust: str  # fact | inferred | uncertain
    weight: float
    src_repo: str
    dst_repo: str
    src_node: str
    dst_node: str
    src_label: str
    dst_label: str
    evidence: str
    more: int = 0


class Detector:
    """Finds links between repositories. `languages` are the source languages it reads (empty: any); a language it does
    not read is reported as such, never silently treated as having no dependencies."""

    name = ""
    kind = ""
    languages: tuple[str, ...] = ()

    def detect(self, db: Session, ids: list[str]) -> tuple[list[Link], dict[str, dict]]:
        raise NotImplementedError


class ImportDetector(Detector):
    name = "imports"
    kind = "import"
    languages = IMPORT_LANGS

    def detect(self, db: Session, ids: list[str]) -> tuple[list[Link], dict[str, dict]]:
        pkgs = _packages(db, ids)
        found = _scan_imports(db, ids, pkgs)[:MAX_IMPORT_EDGES] if len(ids) > 1 and pkgs else []
        links = [
            Link(
                "import",
                e["rel"],
                "used" if e["rel"] == "USES" else "imported",
                e["trust"],
                e["weight"],
                e["src_repo"],
                e["dst_repo"],
                e["src"],
                e["dst"],
                e["from"],
                e["to"],
                e["evidence"],
                e["more"],
            )
            for e in found
        ]
        info: dict[str, dict] = {}
        for name, where in pkgs.items():
            for repo, _root in where:
                info.setdefault(repo, {}).setdefault("provides", []).append(name)
        return links, info


class HttpDetector(Detector):
    name = "urls"
    kind = "http"

    def detect(self, db: Session, ids: list[str]) -> tuple[list[Link], dict[str, dict]]:
        links = [
            Link(
                "http",
                "HTTP",
                "used",
                "inferred",
                h["weight"],
                h["src_repo"],
                h["dst_repo"],
                h["src"],
                h["dst"],
                h["from"],
                h["to"],
                h["evidence"],
            )
            for h in _http_links(db, ids)
        ]
        info = {
            repo: {"route_files": n}
            for repo, n in db.execute(
                select(CodeChunk.repo_id, func.count())
                .where(CodeChunk.repo_id.in_(ids), CodeChunk.chunk_type == "routes")
                .group_by(CodeChunk.repo_id)
            )
        }
        return links, info


DETECTORS: list[Detector] = [ImportDetector(), HttpDetector()]


def _coverage(db: Session, ids: list[str], infos: dict[str, dict[str, dict]]) -> dict[str, dict]:
    """Per repository: the languages found, which detectors cannot read them, what it offers other repositories."""
    langs: dict[str, dict[str, int]] = defaultdict(dict)
    for repo, path in db.execute(
        select(CodeChunk.repo_id, CodeChunk.file_path)
        .where(CodeChunk.repo_id.in_(ids), CodeChunk.chunk_type != "pr")
        .distinct()
    ):
        lang = CODE_EXT.get(posixpath.splitext(path)[1].lower())
        if lang and not (_EXAMPLE.search(path) or routes_svc.TESTISH.search(path)):
            langs[repo][lang] = langs[repo].get(lang, 0) + 1
    out: dict[str, dict] = {}
    for repo in ids:
        mine = langs.get(repo, {})
        out[repo] = {
            "languages": mine,
            "unread": [
                {"detector": d.name, "language": lang, "files": n}
                for d in DETECTORS
                if d.languages
                for lang, n in sorted(mine.items(), key=lambda kv: -kv[1])
                if lang not in d.languages
            ],
            "provides": sorted(infos.get("imports", {}).get(repo, {}).get("provides", [])),
            "route_files": infos.get("urls", {}).get(repo, {}).get("route_files", 0),
            "detectors": [d.name for d in DETECTORS],
        }
    return out


# --- the store -----------------------------------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_waiting: dict[str, threading.Timer] = {}
_waiting_lock = threading.Lock()


def _lock(org: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(org, threading.Lock())


def fingerprint(db: Session, org: str) -> tuple[list[str], str]:
    """The repositories of a workspace and a hash of which version of each is indexed."""
    rows = db.execute(
        select(Repository.id, Repository.indexed_sha).where(Repository.org_id == org).order_by(Repository.id)
    ).all()
    blob = json.dumps([DETECTION_VERSION, [[r.id, r.indexed_sha or ""] for r in rows]])
    return [r.id for r in rows], hashlib.sha256(blob.encode()).hexdigest()[:32]


def refresh(db: Session, org: str, force: bool = False) -> LinkState:
    """Work out the workspace's links again when any repository has been indexed since last time, keep what is still
    there, mark what has gone, and note what is new. One run per workspace at a time."""
    from ..telemetry import LINKS, LINKS_NEW

    ids, fp = fingerprint(db, org)
    state = db.get(LinkState, org)
    if state is not None and state.fingerprint == fp and not force:
        return state
    with _lock(org):
        db.expire_all()
        state = db.get(LinkState, org)
        if state is not None and state.fingerprint == fp and not force:
            return state  # another request did it while this one waited
        started = time.monotonic()
        found: list[Link] = []
        infos: dict[str, dict[str, dict]] = {}
        failed: set[str] = set()
        for d in DETECTORS:
            try:
                links, info = d.detect(db, ids) if len(ids) > 1 else ([], {})
            except Exception:  # one finder failing must not wipe what the others know
                log.exception("link detector %s failed", d.name)
                failed.add(d.kind)
                continue
            found += links
            infos[d.name] = info
        now = datetime.now(UTC)
        created, removed = _store(db, org, found, now, failed)
        first = state is None
        current = state if state is not None else LinkState(org_id=org, baseline_at=now)
        if first:
            db.add(current)
        current.fingerprint = fp
        current.computed_at = now
        current.seconds = round(time.monotonic() - started, 2)
        current.coverage = {**_coverage(db, ids, infos), "_failed": sorted(failed)}
        db.commit()
        active = db.execute(
            select(RepoLink.kind, func.count())
            .where(RepoLink.org_id == org, RepoLink.gone_at.is_(None))
            .group_by(RepoLink.kind)
        ).all()
        for kind, n in active:
            LINKS.labels(kind).set(n)
        if not first and created:
            LINKS_NEW.inc(created)
        log.info(
            "dependencies between repositories: %d found, %d new, %d gone (%.1fs)",
            len(found),
            created,
            removed,
            current.seconds,
        )
        return current


def _store(db: Session, org: str, found: list[Link], now: datetime, failed: set[str]) -> tuple[int, int]:
    existing = {
        (r.kind, r.src_node_id, r.dst_node_id): r for r in db.scalars(select(RepoLink).where(RepoLink.org_id == org))
    }
    seen: set[tuple[str, str, str]] = set()
    created = removed = 0
    for link in found:
        key = (link.kind, link.src_node, link.dst_node)
        if key in seen:
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = RepoLink(
                org_id=org, kind=link.kind, src_node_id=link.src_node, dst_node_id=link.dst_node, first_seen_at=now
            )
            db.add(row)
            created += 1
        elif row.gone_at is not None:  # it came back: new again
            row.first_seen_at, row.gone_at = now, None
            created += 1
        row.rel, row.tier, row.trust, row.weight = link.rel, link.tier, link.trust, link.weight
        row.src_repo_id, row.dst_repo_id = link.src_repo, link.dst_repo
        row.src_label, row.dst_label, row.evidence, row.more = (
            link.src_label[:600],
            link.dst_label[:600],
            link.evidence,
            link.more,
        )
        row.last_seen_at = now
    for key, row in existing.items():
        if key not in seen and row.gone_at is None and row.kind not in failed:
            row.gone_at = now
            removed += 1
    cutoff = now - timedelta(days=KEEP_GONE_DAYS)
    for row in existing.values():
        if row.gone_at is not None and row.gone_at.replace(tzinfo=row.gone_at.tzinfo or UTC) < cutoff:
            db.delete(row)
    db.flush()
    return created, removed


def rows(db: Session, org: str, ids: list[str], kind: str) -> list[dict]:
    """The stored links of one kind between the given repositories, in the shape the graph code uses."""
    refresh(db, org)
    q = select(RepoLink).where(
        RepoLink.org_id == org,
        RepoLink.kind == kind,
        RepoLink.gone_at.is_(None),
        RepoLink.src_repo_id.in_(ids),
        RepoLink.dst_repo_id.in_(ids),
    )
    return [
        {
            "rel": r.rel,
            "src": r.src_node_id,
            "dst": r.dst_node_id,
            "src_repo": r.src_repo_id,
            "dst_repo": r.dst_repo_id,
            "from": r.src_label,
            "to": r.dst_label,
            "evidence": r.evidence,
            "weight": r.weight,
            "trust": r.trust,
            "more": r.more,
        }
        for r in db.scalars(q.order_by(RepoLink.weight.desc(), RepoLink.src_label, RepoLink.dst_label))
    ]


def coverage(db: Session, org: str, ids: list[str]) -> dict[str, dict]:
    state = refresh(db, org)
    return {i: state.coverage.get(i, {}) for i in ids}


def after_index(org: str, delay: float | None = None, timer=threading.Timer) -> None:  # type: ignore[no-untyped-def]
    """Called when an index finishes: work the links out again once indexing goes quiet, so a new dependency is noticed
    even when nobody has the page open. With inline jobs (tests) it runs at once."""
    s = get_settings()
    if not s.auto_link_refresh:
        return
    if s.run_jobs_inline:
        _refresh_in_own_session(org)
        return
    with _waiting_lock:
        old = _waiting.pop(org, None)
        if old is not None:
            old.cancel()
        t = timer(s.auto_link_delay_s if delay is None else delay, _refresh_in_own_session, args=(org,))
        t.daemon = True
        _waiting[org] = t
        t.start()


def _refresh_in_own_session(org: str) -> None:
    from ..db import session_factory

    with _waiting_lock:
        _waiting.pop(org, None)
    try:
        with session_factory()() as db:
            refresh(db, org)
    except Exception:
        log.exception("could not work out the dependencies between repositories")


def forget_repo(db: Session, org: str, repo_id: str) -> None:
    """A deleted repository takes its links with it; the next look works the rest out again."""
    for row in db.scalars(
        select(RepoLink).where(
            RepoLink.org_id == org, (RepoLink.src_repo_id == repo_id) | (RepoLink.dst_repo_id == repo_id)
        )
    ):
        db.delete(row)
    state = db.get(LinkState, org)
    if state is not None:
        state.fingerprint = ""


# --- what depends on this, what a repository offers, what is new -----------------------------------------------------


def _owners(db: Session, wanted: dict[str, set[str]]) -> dict[str, list[dict]]:
    """Who has changed these files most in the last year, by repository and path ("repo:path" -> top two people)."""
    from .ownership import BOT

    cutoff = datetime.now(UTC) - timedelta(days=365)
    out: dict[str, list[dict]] = {}
    for repo, paths in wanted.items():
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for author, files in db.execute(
            select(Change.author, Change.files)
            .where(Change.repo_id == repo, Change.committed_at >= cutoff)
            .order_by(Change.committed_at.desc())
            .limit(1000)
        ):
            if not author or BOT.search(author):
                continue
            for f in files or []:
                if f.get("path") in paths:
                    counts[f["path"]][author] += 1
        for path in paths:
            top = sorted(counts[path].items(), key=lambda kv: (-kv[1], kv[0]))[:2]
            out[f"{repo}:{path}"] = [{"name": a, "commits": n} for a, n in top]
    return out


def impact(db: Session, org: str, scope: Scope, node_id: str) -> dict | None:
    """Everything in other repositories that depends on this class, function or file (or on anything defined in the
    file), who changes those places most, and the callers one step further in. None when the node is not visible."""
    refresh(db, org)
    node = db.get(KGNode, node_id)
    if node is None or node.org_id != org or not scope.can(node.repo_id):
        return None
    targets = {node.id}
    if node.type == "file":
        targets |= set(
            db.scalars(
                select(KGNode.id).where(
                    KGNode.repo_id == node.repo_id, KGNode.path == node.path, KGNode.type.in_(("function", "class"))
                )
            )
        )
    found = db.scalars(
        select(RepoLink)
        .where(RepoLink.org_id == org, RepoLink.dst_node_id.in_(list(targets)), RepoLink.gone_at.is_(None))
        .order_by(RepoLink.src_repo_id, RepoLink.src_label)
    ).all()
    seen = [r for r in found if scope.can(r.src_repo_id)]
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == org))}
    owners = _owners(db, _group_paths(seen))
    direct: list[dict[str, Any]] = [
        {
            "link": r.id,
            "repo_id": r.src_repo_id,
            "repo": names.get(r.src_repo_id, ""),
            "label": r.src_label,
            "target": r.dst_label,
            "kind": r.kind,
            "rel": r.rel,
            "trust": r.trust,
            "evidence": r.evidence,
            "more": r.more,
            "since": r.first_seen_at,
            "owners": owners.get(f"{r.src_repo_id}:{r.src_label.rsplit(':', 1)[0]}", []),
        }
        for r in seen
    ]
    # one step further: functions in the same repository that call a function which depends on this
    callers = {r.src_node_id: r for r in seen}
    indirect: list[dict[str, Any]] = []
    if callers:
        edges = db.execute(
            select(KGEdge.src_id, KGEdge.dst_id)
            .where(KGEdge.dst_id.in_(list(callers)), KGEdge.rel == "CALLS", KGEdge.weight >= 0.5)
            .order_by(KGEdge.weight.desc(), KGEdge.id)
            .limit(400)
        ).all()
        nodes = nodes_by_id(db, {e.src_id for e in edges})
        per: dict[str, int] = defaultdict(int)
        for src, dst in edges:
            n = nodes.get(src)
            if n is None or not scope.can(n.repo_id) or per[dst] >= 3 or src in callers:
                continue
            per[dst] += 1
            indirect.append(
                {
                    "repo": names.get(n.repo_id, ""),
                    "label": f"{n.path}:{n.qualname or n.name}",
                    "through": callers[dst].src_label,
                }
            )
    by_repo: dict[str, int] = defaultdict(int)
    for d in direct:
        by_repo[d["repo"]] += 1 + d["more"]
    return {
        "node": {
            "id": node.id,
            "name": node.qualname or node.name,
            "type": node.type,
            "path": node.path,
            "repo": names.get(node.repo_id, ""),
        },
        "direct": direct,
        "indirect": indirect[:30],
        "repos": [{"repo": r, "uses": n} for r, n in sorted(by_repo.items(), key=lambda kv: -kv[1])],
        "hidden": len(found) - len(seen),
        "note": "Only repositories RepoLens has indexed, and that you can open, are searched. Dependencies "
        "through a database, a queue or configuration are not visible.",
    }


def _group_paths(rs: Sequence[RepoLink]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for r in rs:
        out[r.src_repo_id].add(r.src_label.rsplit(":", 1)[0])
    return out


_ALL = re.compile(r"__all__\s*=\s*[\[(]([^\])]*)[\])]")


def surface(db: Session, org: str, scope: Scope, repo_id: str) -> dict | None:
    """What other repositories use from this one, most used first, and what the package offers that nobody uses."""
    refresh(db, org)
    repo = db.get(Repository, repo_id)
    if repo is None or repo.org_id != org or not scope.can(repo.id):
        return None
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == org))}
    found = db.scalars(
        select(RepoLink).where(RepoLink.org_id == org, RepoLink.dst_repo_id == repo_id, RepoLink.gone_at.is_(None))
    ).all()
    seen = [r for r in found if scope.can(r.src_repo_id)]
    grouped: dict[str, list[RepoLink]] = defaultdict(list)
    for r in seen:
        grouped[r.dst_node_id].append(r)
    nodes = nodes_by_id(db, set(grouped))
    symbols: list[dict[str, Any]] = []
    for nid, rs in grouped.items():
        n = nodes.get(nid)
        symbols.append(
            {
                "id": nid,
                "name": (n.qualname or n.name) if n else rs[0].dst_label.rsplit(":", 1)[-1],
                "type": n.type if n else "",
                "path": n.path if n else rs[0].dst_label.rsplit(":", 1)[0],
                "uses": sum(1 + r.more for r in rs),
                "repos": sorted({names.get(r.src_repo_id, "") for r in rs}),
                "kinds": sorted({r.kind for r in rs}),
                "trust": "fact" if any(r.trust == "fact" for r in rs) else "inferred",
            }
        )
    symbols.sort(key=lambda s: (-s["uses"], s["name"]))
    used = set(grouped)
    exports: list[dict[str, Any]] = []
    resolver = _Resolver(db)
    for pkg, where in _packages(db, [repo_id]).items():
        for _repo, root in where:
            text = resolver.text(repo_id, root + "/__init__.py")
            offered = {a for _, _, ns in _REEXPORT.findall(text) for _, a in _names(ns)}
            for blob in _ALL.findall(text):
                offered |= {x.strip().strip("'\"") for x in blob.split(",") if x.strip().strip("'\"")}
            for name in sorted(offered):
                hit = resolver.symbol(repo_id, root, "", name)
                exports.append(
                    {
                        "name": name,
                        "package": pkg,
                        "node": hit[0] if hit else None,
                        "used": bool(hit and hit[0] in used),
                    }
                )
    unused = [e for e in exports if not e["used"] and e["node"]]
    unused_nodes = nodes_by_id(db, {e["node"] for e in unused})
    return {
        "repo": {"id": repo.id, "name": repo.name},
        "symbols": symbols[:100],
        "dependants": sorted({names.get(r.src_repo_id, "") for r in seen}),
        "hidden": len(found) - len(seen),
        "exports": len(exports),
        "unused_exports": [
            {
                "name": e["name"],
                "package": e["package"],
                "path": unused_nodes[e["node"]].path if e["node"] in unused_nodes else "",
            }
            for e in unused
        ],
        "note": "Counts the repositories RepoLens has indexed and you can open. An export no repository here "
        "uses may still be used by software outside this workspace.",
    }


def changes(db: Session, org: str, scope: Scope, ids: list[str], days: int) -> dict:
    """Dependencies between the given repositories that appeared or disappeared in the last `days` days. Links that
    already existed when tracking began are not reported as new."""
    state = refresh(db, org)
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == org))}
    allowed = [i for i in ids if scope.can(i)]
    baseline = state.baseline_at if state.baseline_at.tzinfo else state.baseline_at.replace(tzinfo=UTC)
    base = [RepoLink.org_id == org, RepoLink.src_repo_id.in_(allowed), RepoLink.dst_repo_id.in_(allowed)]
    new = db.scalars(
        select(RepoLink)
        .where(*base, RepoLink.gone_at.is_(None), RepoLink.first_seen_at >= since, RepoLink.first_seen_at > baseline)
        .order_by(RepoLink.first_seen_at.desc())
    ).all()
    gone = db.scalars(
        select(RepoLink)
        .where(*base, RepoLink.gone_at.is_not(None), RepoLink.gone_at >= since)
        .order_by(RepoLink.gone_at.desc())
    ).all()

    def pairs(rs: Sequence[RepoLink], stamp: str) -> list[dict]:
        out: dict[tuple[str, str], dict] = {}
        for r in rs:
            p = out.setdefault(
                (r.src_repo_id, r.dst_repo_id),
                {
                    "src": r.src_repo_id,
                    "dst": r.dst_repo_id,
                    "from": names.get(r.src_repo_id, ""),
                    "to": names.get(r.dst_repo_id, ""),
                    "count": 0,
                    "examples": [],
                },
            )
            p["count"] += 1 + r.more
            if len(p["examples"]) < 3:
                p["examples"].append(
                    {"from": r.src_label, "to": r.dst_label, "evidence": r.evidence, "at": getattr(r, stamp)}
                )
        return sorted(out.values(), key=lambda p: (-p["count"], p["from"], p["to"]))

    return {
        "days": days,
        "tracking_since": baseline,
        "new": pairs(new, "first_seen_at"),
        "removed": pairs(gone, "gone_at"),
        "computed_at": state.computed_at,
    }
