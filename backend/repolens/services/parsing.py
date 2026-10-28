"""Tree-sitter based symbol extraction with a line-window fallback for unsupported languages."""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".md": "markdown",
    ".rst": "markdown",
    ".mdx": "markdown",
    ".txt": "markdown",
    ".adoc": "markdown",
    ".yaml": "config",
    ".yml": "config",
    ".toml": "config",
    ".json": "config",
    ".sh": "text",
    ".sql": "text",
    ".rb": "text",
    ".rs": "text",
    ".c": "text",
    ".h": "text",
    ".cpp": "text",
    ".cs": "text",
    ".kt": "text",
    # configuration and deployment files (the usual home of "it broke after the config change")
    ".ini": "config",
    ".cfg": "config",
    ".conf": "config",
    ".properties": "config",
    ".tf": "config",
    ".tfvars": "config",
    ".hcl": "config",
    ".gradle": "config",
    ".xml": "config",
    ".proto": "config",
    ".graphql": "config",
    ".tpl": "config",
    ".csproj": "config",  # package manifests the Graph reads (.NET, Gradle Kotlin, gems)
    ".kts": "config",
    ".gemspec": "config",
    ".j2": "config",
    ".example": "config",  # .env.example, settings.ini.example ...
    ".sample": "config",
    ".template": "config",
}
# files recognised by name rather than extension
SPECIAL_FILES = {
    "dockerfile",
    "containerfile",
    "makefile",
    "jenkinsfile",
    "procfile",
    "vagrantfile",
    "go.mod",
    "gemfile",
}
# real environment files hold secrets; only their .example/.sample/.template variants are indexed
SECRET_FILE_RE = re.compile(r"^\.env(\..+)?$|\.(pem|key|p12|pfx|jks|keystore)$", re.I)
EXAMPLE_ENV_RE = re.compile(r"\.(example|sample|template|dist)$", re.I)
SKIP_DIRS = {
    "node_modules",
    ".git",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    ".next",
    ".idea",
    "coverage",
}
SKIP_SUFFIX = (
    ".min.js",
    ".lock",
    ".map",
    ".svg",
    ".png",
    ".jpg",
    ".gif",
    ".pdf",
    ".zip",
    ".jar",
    "package-lock.json",
)

# node types that represent a symbol definition, per language
DEF_NODES = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "go": {"function_declaration": "function", "method_declaration": "method", "type_declaration": "type"},
    "java": {
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
        "constructor_declaration": "method",
    },
}
CONTAINERS = {"class_definition", "class_declaration", "interface_declaration"}
CALL_NODES = {"call", "call_expression", "method_invocation"}
IMPORT_NODES = {"import_statement", "import_from_statement", "import_declaration"}


@dataclass
class ParsedChunk:
    symbol: str
    parent: str
    chunk_type: str
    start_line: int  # 1-based inclusive
    end_line: int
    content: str
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def _special(name: str) -> bool:
    low = name.lower()
    return low in SPECIAL_FILES or low.startswith(("dockerfile.", "containerfile.")) or low.endswith(".dockerfile")


def should_index(path: str, size: int, max_bytes: int) -> bool:
    p = PurePosixPath(path)
    if any(part in SKIP_DIRS for part in p.parts):
        return False
    if path.endswith(SKIP_SUFFIX) or size > max_bytes:
        return False
    if SECRET_FILE_RE.search(p.name) and not EXAMPLE_ENV_RE.search(p.name):
        return False  # .env, *.pem, *.key ... never enter the index
    return _special(p.name) or p.suffix.lower() in LANG_BY_EXT


def detect_language(path: str) -> str:
    p = PurePosixPath(path)
    if _special(p.name):
        return "config"
    return LANG_BY_EXT.get(p.suffix.lower(), "text")


_parsers: dict = {}


def _parser(lang: str):
    if lang in _parsers:
        return _parsers[lang]
    p = None
    try:
        from tree_sitter import Language, Parser

        mod = {
            "python": "tree_sitter_python",
            "javascript": "tree_sitter_javascript",
            "go": "tree_sitter_go",
            "java": "tree_sitter_java",
            "typescript": "tree_sitter_typescript",
            "tsx": "tree_sitter_typescript",
        }.get(lang)
        if mod:
            import importlib

            m = importlib.import_module(mod)
            fn = {"typescript": "language_typescript", "tsx": "language_tsx"}.get(lang, "language")
            p = Parser(Language(getattr(m, fn)()))
    except Exception:  # parser unavailable -> fallback chunking
        p = None
    _parsers[lang] = p
    return p


def _name_of(node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    if n is None and node.type == "type_declaration":  # go: type_declaration -> type_spec -> name
        for c in node.children:
            if c.type == "type_spec":
                n = c.child_by_field_name("name")
    return src[n.start_byte : n.end_byte].decode(errors="replace") if n is not None else ""


def _collect(node, src: bytes, kinds: set[str], out: list[str]) -> None:
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in kinds:
            f = cur.child_by_field_name("function") or cur.child_by_field_name("name")
            if f is not None:
                txt = src[f.start_byte : f.end_byte].decode(errors="replace")
                out.append(txt.split("(")[0].split(".")[-1].strip())
        stack.extend(cur.children)


def parse_file(path: str, text: str) -> list[ParsedChunk]:
    lang = detect_language(path)
    lines = text.splitlines()
    if not lines:
        return []
    if lang == "markdown":
        from .docs import parse_markdown

        return parse_markdown(path, text)
    parser = _parser(lang)
    chunks: list[ParsedChunk] = []
    if parser is not None and lang in DEF_NODES:
        src = text.encode()
        tree = parser.parse(src)
        defs = DEF_NODES[lang]
        file_imports: list[str] = []
        _imports(tree.root_node, src, file_imports)

        def walk(node, parent: str):
            for child in node.children:
                if child.type in defs:
                    name = _name_of(child, src)
                    kind = defs[child.type]
                    is_container = child.type in CONTAINERS
                    start, end = child.start_point[0] + 1, child.end_point[0] + 1
                    if is_container:
                        # container header chunk (class docstring/signature) kept short; members chunked separately
                        end_hdr = min(end, start + 12)
                        chunks.append(
                            ParsedChunk(
                                name,
                                parent,
                                kind,
                                start,
                                end_hdr,
                                "\n".join(lines[start - 1 : end_hdr]),
                                [],
                                file_imports,
                            )
                        )
                        walk(child, f"{parent}.{name}".strip(".") if parent else name)
                    else:
                        calls: list[str] = []
                        _collect(child, src, CALL_NODES, calls)
                        chunks.append(
                            ParsedChunk(
                                name,
                                parent,
                                kind,
                                start,
                                end,
                                "\n".join(lines[start - 1 : end]),
                                sorted(set(calls))[:60],
                                file_imports,
                            )
                        )
                else:
                    walk(child, parent)

        walk(tree.root_node, "")
        if chunks:
            return _split_large(chunks, lines)
    return _fallback_chunks(lines, lang)


def _imports(root, src: bytes, out: list[str]) -> None:
    for c in root.children:
        if c.type in IMPORT_NODES or c.type == "import_declaration":
            out.append(src[c.start_byte : c.end_byte].decode(errors="replace").strip()[:200])
        elif c.type == "expression_statement":
            continue


def _split_large(chunks: list[ParsedChunk], lines: list[str], max_lines: int = 120) -> list[ParsedChunk]:
    out: list[ParsedChunk] = []
    for c in chunks:
        if c.end_line - c.start_line + 1 <= max_lines:
            out.append(c)
            continue
        for s in range(c.start_line, c.end_line + 1, max_lines):
            e = min(s + max_lines - 1, c.end_line)
            out.append(
                ParsedChunk(c.symbol, c.parent, c.chunk_type, s, e, "\n".join(lines[s - 1 : e]), c.calls, c.imports)
            )
    return out


def _fallback_chunks(lines: list[str], lang: str, window: int = 60, overlap: int = 10) -> list[ParsedChunk]:
    kind = {"markdown": "doc", "config": "config"}.get(lang, "block")
    out = []
    step = window - overlap
    for s in range(0, len(lines), step):
        e = min(s + window, len(lines))
        body = "\n".join(lines[s:e])
        symbol = ""
        if kind == "doc":
            for ln in lines[s:e]:
                if ln.startswith("#"):
                    symbol = ln.lstrip("# ").strip()
                    break
        out.append(ParsedChunk(symbol, "", kind, s + 1, e, body))
        if e == len(lines):
            break
    return out
