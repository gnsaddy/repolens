"""Imports in Go, Java, Kotlin, C#, Rust, C and C++: which other repository a file takes code from, and what it takes.

Python and JavaScript/TypeScript imports are read from what the parser stored (`repo_links._scan_imports`). The parser
keeps no import lines for these languages, so they are read here from the text of each file. Every language has the same
shape: say what a repository offers (a Go module, a Java package, a Rust crate, a C# namespace, a header), read each
file's import lines, and link a file to the class, function or file of another repository that the line names. Each edge
carries the line as its proof. An import that cannot be placed in another repository makes no edge, and a name that more
than one other repository could mean is reported as `inferred`, never as a fact."""

import posixpath
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import CodeChunk, KGNode
from . import manifests

LANG_EXT = {
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
}
LANGS = tuple(dict.fromkeys(LANG_EXT.values()))
HEADER_EXT = (".h", ".hpp", ".hh", ".hxx", ".inc")
WORDS_KEPT = 4000  # identifiers kept per C# file, to see which type of a namespace it uses
SYMBOLS_PER_IMPORT = 5
RUST_ROOTS = {"crate", "self", "super", "std", "core", "alloc"}

_GO_BLOCK = re.compile(r"^[ \t]*import[ \t]*\((.*?)\)", re.M | re.S)
_GO_LINE = re.compile(r"""^[ \t]*(?:(\w+|\.|_)[ \t]+)?"([^"\n]+)"[ \t]*(?://.*)?$""", re.M)
_GO_ONE = re.compile(r"""^[ \t]*import[ \t]+(?:(\w+|\.|_)[ \t]+)?"([^"\n]+)\"""", re.M)
_JVM_PACKAGE = re.compile(r"^[ \t]*package[ \t]+([\w.]+)", re.M)
_JVM_IMPORT = re.compile(
    r"^[ \t]*import[ \t]+(static[ \t]+)?([\w.]+?)(\.\*)?[ \t]*(?:as[ \t]+\w+)?[ \t]*;?[ \t]*$", re.M
)
_C_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\n]+)[>"]', re.M)
_CS_USING = re.compile(
    r"^[ \t]*(?:global[ \t]+)?using[ \t]+(?:static[ \t]+)?(?:\w+[ \t]*=[ \t]*)?([\w.]+)[ \t]*;", re.M
)
_CS_NAMESPACE = re.compile(r"^[ \t]*namespace[ \t]+([\w.]+)", re.M)
_CS_TYPE = re.compile(r"\b(?:class|interface|struct|record|enum)[ \t]+(\w+)")
_RS_USE = re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?use[ \t]+([^;]+);", re.M)
_QUALIFIED = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
_WORD = re.compile(r"\b[A-Z]\w{2,}\b")
_VERSION = re.compile(r"^v\d+$")


def _stmt(text: str) -> str:
    return " ".join(text.split())[:200]


# --- reading the import lines -------------------------------------------------------------------------------------


def parse_go(text: str) -> list[tuple[str, str, str]]:
    """[(import path, alias or "", the line)] for `import "x"`, `import a "x"` and `import ( ... )` blocks."""
    out = [(m.group(2), m.group(1) or "", m.group(0)) for m in _GO_ONE.finditer(text)]
    for block in _GO_BLOCK.findall(text):
        out += [(m.group(2), m.group(1) or "", m.group(0)) for m in _GO_LINE.finditer(block)]
    return [(spec, alias, _stmt(f'import {alias} "{spec}"')) for spec, alias, _ in out]


def parse_jvm(text: str) -> list[tuple[str, bool, bool]]:
    """[(dotted name, static, wildcard)] for the imports of a Java or Kotlin file."""
    return [(m.group(2), bool(m.group(1)), bool(m.group(3))) for m in _JVM_IMPORT.finditer(text)]


def parse_includes(text: str) -> list[tuple[str, str]]:
    """[(path, "<" or '"')] for the `#include` lines of a C or C++ file."""
    return [(m.group(2).strip(), m.group(1)) for m in _C_INCLUDE.finditer(text)]


def parse_using(text: str) -> list[str]:
    return [m.group(1) for m in _CS_USING.finditer(text)]


def _split_top(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [*parts, cur] if cur else parts


def expand_use(expr: str) -> list[str]:
    """`a::{b, c::D as E}` -> [`a::b`, `a::c::D`]: the paths a Rust `use` names."""
    return [p[: -len("::self")] if p.endswith("::self") else p for p in _expand(expr)]


def _expand(expr: str) -> list[str]:
    expr = "".join(re.sub(r"\s+as\s+\w+", "", expr).split())
    i = expr.find("{")
    if i < 0:
        return [expr] if expr else []
    depth = 0
    for j in range(i, len(expr)):
        depth += expr[j] == "{"
        depth -= expr[j] == "}"
        if depth == 0:
            break
    out = []
    for part in _split_top(expr[i + 1 : j]):
        out += [expr[:i] + e for e in _expand(part)]
    return out


def parse_use(text: str) -> list[tuple[str, str]]:
    """[(path, the statement)] for the `use` lines of a Rust file."""
    out = []
    for m in _RS_USE.finditer(text):
        out += [(p, _stmt(f"use {m.group(1)};")) for p in expand_use(m.group(1))]
    return out


# --- what each file says ------------------------------------------------------------------------------------------


@dataclass
class FileFacts:
    repo: str
    path: str
    lang: str
    package: str = ""
    go: list[tuple[str, str, str]] = field(default_factory=list)
    jvm: list[tuple[str, bool, bool]] = field(default_factory=list)
    includes: list[tuple[str, str]] = field(default_factory=list)
    using: list[str] = field(default_factory=list)
    uses: list[tuple[str, str]] = field(default_factory=list)
    namespaces: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    qualified: set[tuple[str, str]] = field(default_factory=set)
    words: set[str] = field(default_factory=set)


def _read(f: FileFacts, text: str) -> None:
    lang = f.lang
    if lang == "go":
        f.go += [i for i in parse_go(text) if i not in f.go]
        if len(f.qualified) < 800:
            f.qualified |= {(m.group(1), m.group(2)) for m in _QUALIFIED.finditer(text)}
    elif lang in ("java", "kotlin"):
        f.jvm += [i for i in parse_jvm(text) if i not in f.jvm]
        if pkg := _JVM_PACKAGE.search(text):
            f.package = pkg.group(1)
    elif lang in ("c", "cpp"):
        f.includes += [i for i in parse_includes(text) if i not in f.includes]
    elif lang == "csharp":
        f.using += [u for u in parse_using(text) if u not in f.using]
        f.namespaces |= set(_CS_NAMESPACE.findall(text))
        f.types |= set(_CS_TYPE.findall(text))
        if len(f.words) < WORDS_KEPT:
            f.words |= set(_WORD.findall(text))
    elif lang == "rust":
        f.uses += [u for u in parse_use(text) if u not in f.uses]


def _files(db: Session, ids: list[str], skip: Callable[[str], bool]) -> dict[tuple[str, str], FileFacts]:
    out: dict[tuple[str, str], FileFacts] = {}
    rows = db.execute(
        select(CodeChunk.repo_id, CodeChunk.file_path, CodeChunk.content, CodeChunk.imports)
        .where(
            CodeChunk.repo_id.in_(ids),
            CodeChunk.chunk_type != "pr",
            or_(*[CodeChunk.file_path.like(f"%{ext}") for ext in LANG_EXT]),
        )
        .execution_options(yield_per=500)
    )
    for repo, path, content, imports in rows:
        lang = LANG_EXT.get(posixpath.splitext(path)[1].lower())
        if not lang or skip(path):
            continue
        f = out.setdefault((repo, path), FileFacts(repo, path, lang))
        _read(f, "\n".join([*(imports or []), content or ""]))
    return out


# --- what each repository offers ----------------------------------------------------------------------------------


@dataclass
class Offers:
    go: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))  # module -> (repo, dir)
    rust: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))  # crate -> (repo, dir)
    # class -> (repo, path, whether the file says its package itself)
    jvm: dict[str, list[tuple[str, str, bool]]] = field(default_factory=lambda: defaultdict(list))
    csharp: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # namespace -> repos


def package_from_path(path: str) -> str:
    """The package a Java or Kotlin file is in by where it lies (`src/main/java/com/acme/User.java` -> `com.acme`), for
    when the parser did not keep its `package` line."""
    parts = posixpath.dirname(path).split("/")
    for root in ("java", "kotlin"):
        if root in parts:
            return ".".join(p for p in parts[len(parts) - parts[::-1].index(root) :] if p)
    return ".".join(parts[parts.index("src") + 1 :]) if "src" in parts else ""


def _manifest_offers(db: Session, ids: list[str], skip: Callable[[str], bool]) -> Offers:
    offers = Offers()
    rows = db.execute(
        select(CodeChunk.repo_id, CodeChunk.file_path, CodeChunk.content).where(
            CodeChunk.repo_id.in_(ids),
            or_(CodeChunk.file_path.like("%go.mod"), CodeChunk.file_path.like("%Cargo.toml")),
        )
    )
    for repo, path, content in rows:
        name = posixpath.basename(path)
        if name not in ("go.mod", "Cargo.toml") or skip(path):
            continue
        m = manifests.parse(path, content or "")
        for provided, _line in m.provides:
            if name == "go.mod":
                offers.go[provided].append((repo, posixpath.dirname(path)))
            else:
                offers.rust[provided.replace("-", "_")].append((repo, posixpath.dirname(path)))
    return offers


def offers_of(
    db: Session, ids: list[str], files: dict[tuple[str, str], FileFacts], skip: Callable[[str], bool]
) -> Offers:
    offers = _manifest_offers(db, ids, skip)
    for (repo, path), f in files.items():
        if f.lang in ("java", "kotlin"):
            stem = posixpath.splitext(posixpath.basename(path))[0]
            package = f.package or package_from_path(path)
            if package:
                offers.jvm[f"{package}.{stem}"].append((repo, path, bool(f.package)))
        elif f.lang == "csharp":
            for ns in f.namespaces:
                offers.csharp[ns].add(repo)
    return offers


def provides(offers: Offers, files: dict[tuple[str, str], FileFacts]) -> dict[str, list[str]]:
    """What each repository offers other repositories, by name (for the coverage report)."""
    out: dict[str, set[str]] = defaultdict(set)
    for table in (offers.go, offers.rust):
        for name, where in table.items():
            for repo, _ in where:
                out[repo].add(name)
    for ns, repos in offers.csharp.items():
        for repo in repos:
            out[repo].add(ns)
    pkgs = {(f.repo, f.package) for f in files.values() if f.package}
    for repo, pkg in pkgs:
        out[repo].add(pkg)
    return {repo: sorted(names) for repo, names in out.items()}


# --- linking ------------------------------------------------------------------------------------------------------


class _Index:
    """Looks up the nodes a link can end in."""

    def __init__(self, db: Session, ids: list[str], skip: Callable[[str], bool]):
        self.db = db
        self.ids = ids
        self.file: dict[str, dict[str, str]] = defaultdict(dict)  # repo -> path -> node id
        for repo, path, nid in db.execute(
            select(KGNode.repo_id, KGNode.path, KGNode.id).where(KGNode.repo_id.in_(ids), KGNode.type == "file")
        ):
            if not skip(path):
                self.file[repo][path] = nid
        self.by_base: dict[str, list[tuple[str, str]]] = defaultdict(list)  # header basename -> (repo, path)
        for repo, paths in self.file.items():
            for p in paths:
                if p.lower().endswith(HEADER_EXT):
                    self.by_base[posixpath.basename(p).lower()].append((repo, p))
        self._memo: dict[tuple, list[tuple[str, str, str]]] = {}

    def symbols(self, repo: str, name: str, *, path: str | None = None, prefix: str | None = None):  # type: ignore[no-untyped-def]
        """[(node id, path, name)] of the functions and classes called `name` in a file or under a directory."""
        key = (repo, name, path, prefix)
        if key not in self._memo:
            q = select(KGNode.id, KGNode.path).where(
                KGNode.repo_id == repo,
                KGNode.type.in_(("function", "class")),
                KGNode.name == name,
                KGNode.qualname == name,
            )
            if path is not None:
                q = q.where(KGNode.path == path)
            elif prefix is not None:
                q = q.where(KGNode.path.like(prefix.rstrip("/") + "/%" if prefix else "%"))
            self._memo[key] = [
                (r.id, r.path, name)
                for r in self.db.execute(q.order_by(KGNode.path).limit(5))
                if r.path in self.file[repo]
            ]
        return self._memo[key]


def _edge(
    src: FileFacts, src_node: str, dst_repo: str, dst_node: str, dst_path: str, dst_name: str, line: str, trust: str
) -> dict:
    to = f"{dst_path}:{dst_name}" if dst_name else dst_path
    what = dst_name or dst_path
    return {
        "rel": "IMPORTS",
        "src": src_node,
        "dst": dst_node,
        "src_repo": src.repo,
        "dst_repo": dst_repo,
        "from": src.path,
        "to": to,
        "evidence": f"{src.path} imports {what}: `{line}`",
        "weight": 1.0 if trust == "fact" else 0.7,
        "trust": trust,
        "more": 0,
    }


def _go_packages(spec: str, f: FileFacts, offers: Offers, ix: _Index) -> list[tuple[str, str, list[str]]]:
    """[(repository, directory, its Go files)] of the other repositories' package an import path names."""
    out = []
    for module in sorted((m for m in offers.go if spec == m or spec.startswith(m + "/")), key=len, reverse=True):
        for repo, base in offers.go[module]:
            folder = posixpath.join(base, spec[len(module) :].strip("/"))
            files = sorted(
                p
                for p in ix.file[repo]
                if posixpath.dirname(p) == folder and p.endswith(".go") and not p.endswith("_test.go")
            )
            if repo != f.repo and files:
                out.append((repo, folder, files))
        if out:
            break  # the longest module that matches is the one the path belongs to
    return out


def _go(f: FileFacts, offers: Offers, ix: _Index, add: Callable[[dict], None]) -> None:
    src = ix.file[f.repo].get(f.path)
    for spec, alias, line in f.go:
        parts = [s for s in spec.split("/") if s]
        qualifier = (
            alias
            if alias not in ("", ".", "_")
            else next((p for p in reversed(parts) if not _VERSION.match(p)), parts[-1]).replace("-", "_")
        )
        if not src:
            return
        for repo, folder, files in _go_packages(spec, f, offers, ix):
            named = [
                s
                for q, name in sorted(f.qualified)
                if q == qualifier and name[:1].isupper()
                for s in ix.symbols(repo, name, prefix=folder)
                if posixpath.dirname(s[1]) == folder
            ]
            for node, path, name in named[:SYMBOLS_PER_IMPORT]:
                add(_edge(f, src, repo, node, path, name, line, "fact"))
            if not named:
                add(_edge(f, src, repo, ix.file[repo][files[0]], files[0], "", line, "fact"))


def _jvm(f: FileFacts, offers: Offers, ix: _Index, add: Callable[[dict], None]) -> None:
    src = ix.file[f.repo].get(f.path)
    for name, static, wildcard in f.jvm:
        if wildcard or not src:
            continue
        candidates = offers.jvm.get(name) or (offers.jvm.get(name.rpartition(".")[0]) if static else None) or []
        if any(repo == f.repo for repo, _, _ in candidates):
            continue  # the repository has its own class of that name
        others = [(r, p, said) for r, p, said in candidates if r != f.repo and p in ix.file[r]]
        stmt = f"import {'static ' if static else ''}{name}"
        for repo, path, said in others[:3]:
            stem = posixpath.splitext(posixpath.basename(path))[0]
            hit = ix.symbols(repo, stem, path=path)
            node, label = (hit[0][0], stem) if hit else (ix.file[repo][path], "")
            trust = "fact" if len(others) == 1 and (said or hit) else "inferred"
            add(_edge(f, src, repo, node, path, label, stmt, trust))


def _include_target(f: FileFacts, spec: str, ix: _Index) -> list[tuple[str, str]]:
    base = posixpath.basename(spec).lower()
    want = posixpath.normpath(spec).lower().lstrip("./")
    mine = [
        1
        for repo, p in ix.by_base.get(base, [])
        if repo == f.repo and (p.lower() == want or p.lower().endswith("/" + want))
    ]
    if mine:
        return []  # the repository has that header itself
    return [
        (r, p)
        for r, p in ix.by_base.get(base, [])
        if r != f.repo and (p.lower() == want or p.lower().endswith("/" + want))
    ]


def _c(f: FileFacts, ix: _Index, add: Callable[[dict], None]) -> None:
    src = ix.file[f.repo].get(f.path)
    if not src:
        return
    for spec, delim in f.includes:
        found = _include_target(f, spec, ix)
        repos = {r for r, _ in found}
        if len(repos) != 1:
            continue  # nobody has it, or several repositories do and the line cannot say which
        path = min((p for _, p in found), key=lambda p: (len(p), p))
        trust = "fact" if "/" in spec else "inferred"
        include = f"#include <{spec}>" if delim == "<" else f'#include "{spec}"'
        add(_edge(f, src, found[0][0], ix.file[found[0][0]][path], path, "", include, trust))


def _csharp(
    f: FileFacts,
    files: dict[tuple[str, str], FileFacts],
    offers: Offers,
    ix: _Index,
    add: Callable[[dict], None],
    by_ns: dict[str, list[FileFacts]],
) -> None:
    src = ix.file[f.repo].get(f.path)
    for ns in f.using:
        repos = offers.csharp.get(ns, set())
        if not src or not repos or f.repo in repos:
            continue
        stmt = f"using {ns};"
        used = [
            (g, t)
            for g in by_ns.get(ns, [])
            if g.repo != f.repo and g.path in ix.file[g.repo]
            for t in sorted(g.types)
            if t in f.words
        ]
        for g, t in used[:SYMBOLS_PER_IMPORT]:
            hit = ix.symbols(g.repo, t, path=g.path)
            if hit:
                add(_edge(f, src, g.repo, hit[0][0], g.path, t, stmt, "fact" if len(repos) == 1 else "inferred"))
            else:
                add(
                    _edge(
                        f,
                        src,
                        g.repo,
                        ix.file[g.repo][g.path],
                        g.path,
                        t,
                        stmt,
                        "fact" if len(repos) == 1 else "inferred",
                    )
                )
        if not used:
            anchors = sorted(
                (g for g in by_ns.get(ns, []) if g.repo != f.repo and g.path in ix.file[g.repo]), key=lambda g: g.path
            )
            if anchors:
                add(
                    _edge(
                        f,
                        src,
                        anchors[0].repo,
                        ix.file[anchors[0].repo][anchors[0].path],
                        anchors[0].path,
                        "",
                        stmt,
                        "inferred",
                    )
                )


def _rust(f: FileFacts, offers: Offers, ix: _Index, add: Callable[[dict], None]) -> None:
    src = ix.file[f.repo].get(f.path)
    for path, stmt in f.uses:
        segs = [s for s in path.split("::") if s]
        if not src or len(segs) < 1 or segs[0] in RUST_ROOTS:
            continue
        for repo, base in offers.rust.get(segs[0], []):
            if repo == f.repo:
                continue
            folder = posixpath.join(base, "src")
            known = ix.file[repo]
            file = None
            used = 0
            for seg in segs[1:]:
                nxt = next((p for p in (f"{folder}/{seg}.rs", f"{folder}/{seg}/mod.rs") if p in known), None)
                if not nxt:
                    break
                file, folder, used = nxt, f"{folder}/{seg}", used + 1
            name = segs[1 + used] if len(segs) > 1 + used and segs[1 + used] != "*" else ""
            root = file or next(
                (
                    p
                    for p in (f"{posixpath.join(base, 'src')}/lib.rs", f"{posixpath.join(base, 'src')}/main.rs")
                    if p in known
                ),
                None,
            )
            if root is None:
                continue
            hit = ix.symbols(repo, name, path=root) if name else []
            trust = "fact"
            if not hit and name:
                hit = ix.symbols(repo, name, prefix=posixpath.join(base, "src"))
                trust = "inferred"
            if hit:
                add(_edge(f, src, repo, hit[0][0], hit[0][1], name, stmt, trust))
            elif not name or used:
                add(_edge(f, src, repo, known[root], root, "", stmt, "fact" if used or not name else "inferred"))
            break


def scan(db: Session, ids: Sequence[str], skip: Callable[[str], bool]) -> tuple[list[dict], dict[str, list[str]]]:
    """(edges, what each repository offers) for the languages above. `skip` says which paths to leave out (tests,
    examples, vendored code). Edges have the same shape as the Python and JavaScript ones."""
    ids = list(ids)
    files = _files(db, ids, skip)
    offers = offers_of(db, ids, files, skip)
    offered = provides(offers, files)
    if len(ids) < 2 or not files:
        return [], offered
    ix = _Index(db, ids, skip)
    by_ns: dict[str, list[FileFacts]] = defaultdict(list)
    for f in files.values():
        for ns in f.namespaces:
            by_ns[ns].append(f)
    found: dict[tuple[str, str], dict] = {}

    def add(e: dict) -> None:
        found.setdefault((e["src"], e["dst"]), e)

    for key in sorted(files):
        f = files[key]
        if f.lang == "go":
            _go(f, offers, ix, add)
        elif f.lang in ("java", "kotlin"):
            _jvm(f, offers, ix, add)
        elif f.lang in ("c", "cpp"):
            _c(f, ix, add)
        elif f.lang == "csharp":
            _csharp(f, files, offers, ix, add, by_ns)
        elif f.lang == "rust":
            _rust(f, offers, ix, add)
    return list(found.values()), offered
