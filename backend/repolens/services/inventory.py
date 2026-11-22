"""Inventories written down at index time: environment variables, command line commands and database tables.

Like the route inventory (`routes.py`), each file's items are kept as one searchable summary chunk, because a question
like "list all the environment variables" needs every one, not the eight best matches. Pattern based, so it is a good
inventory rather than a guarantee: names built at run time, or declared in a way these patterns do not know, are not
seen."""

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .parsing import EXAMPLE_ENV_RE, ParsedChunk

ENV, CLI, TABLES = "envvars", "cli", "tables"
TITLES = {ENV: "Environment variables", CLI: "Command line commands", TABLES: "Database tables"}
TYPES = tuple(TITLES)  # the chunk types, so searches that want code only can leave them out
TESTISH = re.compile(r"(^|/)(tests?|__tests__|e2e|spec|fixtures?)(/|$)|\.test\.|\.spec\.|(^|/)test_|_test\.")
MIGRATION = re.compile(r"(^|/)(alembic/versions|migrations?|db/migrate)(/|$)")
MAX_COLUMNS = 8


@dataclass(frozen=True)
class Item:
    name: str
    detail: str
    line: int


# ---------------------------------------------------------------------------------------- environment variables
ENV_CALLS = (
    re.compile(r"""os\.environ(?:\.get|\.setdefault)?\s*[\[(]\s*["']([A-Za-z_]\w*)["']"""),
    re.compile(r"""os\.getenv\(\s*["']([A-Za-z_]\w*)["']"""),
    re.compile(r"""process\.env\.([A-Za-z_]\w*)"""),
    re.compile(r"""process\.env\[\s*["']([A-Za-z_]\w*)["']\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Za-z_]\w*)"""),
    re.compile(r"""os\.(?:Getenv|LookupEnv)\(\s*"([A-Za-z_]\w*)\""""),
    re.compile(r"""System\.getenv\(\s*"([A-Za-z_]\w*)\""""),
    re.compile(r"""\bENV(?:\.fetch\(|\[)\s*["']([A-Za-z_]\w*)["']"""),
)
SETTINGS_CLASS = re.compile(r"^class\s+\w+\(.*\bBaseSettings\b.*\)\s*:")
ENV_PREFIX = re.compile(r"""env_prefix\s*=\s*["']([^"']*)["']""")
SETTING_FIELD = re.compile(r"^ {4}([a-z]\w*)\s*:\s*([^=#]+?)(?:\s*=\s*(.+?))?\s*(?:#.*)?$")
DOTENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)$")


def _is_example_env(name: str) -> bool:
    low = name.lower()
    return (low.startswith(".env") or ".env." in low or low.endswith(".env")) and bool(EXAMPLE_ENV_RE.search(low))


def _short(text: str, n: int = 50) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def env_vars(path: str, text: str) -> list[Item]:
    found: dict[str, Item] = {}

    def add(name: str, detail: str, line: int) -> None:
        if name not in found or (detail and not found[name].detail):
            found[name] = Item(name, detail, line)

    base = PurePosixPath(path).name
    lines = text.splitlines()
    if _is_example_env(base):
        note = ""
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if s.startswith("#"):
                note = s.lstrip("# ").strip()
                continue
            m = DOTENV_LINE.match(s)
            if m:
                add(m.group(1), _short(note) or "listed in the example file", i)
            note = ""
        return sorted(found.values(), key=lambda x: x.line)
    if not path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".java", ".kt", ".rb")):
        return []
    prefix = ENV_PREFIX.search(text)
    in_settings = False
    for i, ln in enumerate(lines, 1):
        if SETTINGS_CLASS.match(ln):
            in_settings = True
            continue
        if in_settings:
            if ln.strip() and not ln.startswith((" ", "\t")):
                in_settings = False
            else:
                f = SETTING_FIELD.match(ln)
                if f and not f.group(1).startswith("model_"):
                    default = f.group(3)
                    add(
                        f"{prefix.group(1) if prefix else ''}{f.group(1)}".upper(),
                        f"setting, default {_short(default, 40)}" if default else "setting, no default",
                        i,
                    )
        for pat in ENV_CALLS:
            for m in pat.finditer(ln):
                add(m.group(1), "read in the code", i)
    return sorted(found.values(), key=lambda x: x.line)


# --------------------------------------------------------------------------------------------------- command line
ARGPARSE_CMD = re.compile(r"""\.add_parser\(\s*["']([\w-]+)["']""")
ARGPARSE_OPT = re.compile(r"""\.add_argument\(\s*["'](--?[\w-]+)["']""")
CLICK_CMD = re.compile(r"""^\s*@(\w+)\.(command|group)\(([^)]*)\)""")
CLICK_NAME = re.compile(r"""^\s*(?:name\s*=\s*)?["']([\w-]+)["']""")
CLICK_OPT = re.compile(r"""^\s*@(?:\w+\.)?option\(\s*["'](--?[\w-]+)["']""")
PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
NODE_CMD = re.compile(r"""\.command\(\s*["']([\w:-]+)""")
NODE_OPT = re.compile(r"""\.(?:option|requiredOption)\(\s*["'](-[^"']+)["']""")
GO_CMD = re.compile(r"""\bUse:\s*"([\w-]+)""")
GO_FLAG = re.compile(r"""(?:Flags\(\)|\bflag)\.\w+\(\s*(?:&\w+,\s*)?"([\w-]+)\"""")
MAKE_TARGET = re.compile(r"^([A-Za-z][\w.-]*)\s*:(?![=:])(.*)$")


def commands(path: str, text: str) -> list[Item]:
    base = PurePosixPath(path).name
    out: list[Item] = []
    if base == "package.json":
        try:
            scripts = json.loads(text).get("scripts") or {}
        except (ValueError, AttributeError):
            return []
        keys = list(scripts) if isinstance(scripts, dict) else []
        for k in keys:
            at = text.find(f'"{k}"')
            out.append(Item(f"npm run {k}", _short(scripts[k], 60), text.count("\n", 0, max(at, 0)) + 1))
        return out
    if base.lower() == "makefile":
        for i, ln in enumerate(text.splitlines(), 1):
            m = MAKE_TARGET.match(ln)
            if m and "%" not in m.group(1) and not m.group(1).startswith("."):
                note = m.group(2).split("##", 1)[1].strip() if "##" in m.group(2) else ""
                out.append(Item(f"make {m.group(1)}", _short(note, 60) or "make target", i))
        return out
    lines = text.splitlines()
    if path.endswith(".py"):
        for i, ln in enumerate(lines, 1):
            for m in ARGPARSE_CMD.finditer(ln):
                out.append(Item(m.group(1), "command", i))
            for m in ARGPARSE_OPT.finditer(ln):
                out.append(Item(m.group(1), "option", i))
            c = CLICK_CMD.match(ln)
            if c and c.group(1) not in ("router", "app_router") and c.group(2) in ("command", "group"):
                named = CLICK_NAME.match(c.group(3))
                name = named.group(1) if named else ""
                if not name:  # the function name, as click and typer do
                    for nxt in lines[i : i + 6]:
                        d = PY_DEF.match(nxt)
                        if d:
                            name = d.group(1).replace("_", "-")
                            break
                if name:
                    out.append(Item(name, "command", i))
            o = CLICK_OPT.match(ln)
            if o:
                out.append(Item(o.group(1), "option", i))
    elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")):
        for i, ln in enumerate(lines, 1):
            for m in NODE_CMD.finditer(ln):
                out.append(Item(m.group(1), "command", i))
            for m in NODE_OPT.finditer(ln):
                out.append(Item(m.group(1), "option", i))
    elif path.endswith(".go"):
        for i, ln in enumerate(lines, 1):
            for m in GO_CMD.finditer(ln):
                out.append(Item(m.group(1), "command", i))
            for m in GO_FLAG.finditer(ln):
                out.append(Item(f"--{m.group(1)}", "option", i))
    return out


# --------------------------------------------------------------------------------------------------- database tables
TABLENAME = re.compile(r"""^\s*__tablename__\s*=\s*["']([\w.]+)["']""")
SQLA_COLUMN = re.compile(r"^ {4}([a-z_]\w*)\s*(?::\s*Mapped\[|=\s*(?:mapped_column|Column)\()")
DJANGO_MODEL = re.compile(r"^class\s+(\w+)\(.*\bmodels\.Model\b.*\)\s*:")
DJANGO_FIELD = re.compile(r"^ {4}([a-z_]\w*)\s*=\s*models\.\w+\(")
CREATE_TABLE = re.compile(r"""CREATE\s+(?:TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\[]?([\w.]+)""", re.I)
SQL_SKIP = ("primary", "foreign", "unique", "constraint", "key", "index", "check", "exclude", "like")
RAILS_TABLE = re.compile(r"""^\s*create_table\s+["']([\w.]+)["']""")
RAILS_COLUMN = re.compile(r"""^\s*t\.\w+\s+["'](\w+)["']""")
JPA_TABLE = re.compile(r"""@Table\(\s*name\s*=\s*"([\w.]+)\"""")


def _columns(names: list[str]) -> str:
    if not names:
        return "table"
    more = f" and {len(names) - MAX_COLUMNS} more" if len(names) > MAX_COLUMNS else ""
    return f"columns {', '.join(names[:MAX_COLUMNS])}{more}"


def tables(path: str, text: str) -> list[Item]:
    if MIGRATION.search(path):  # a migration repeats the model it creates, and many migrations change one table
        return []
    lines = text.splitlines()
    out: list[Item] = []
    if path.endswith(".py"):
        i = 0
        while i < len(lines):
            ln = lines[i]
            django = DJANGO_MODEL.match(ln)
            if django or ln.startswith("class "):
                j = i + 1
                body: list[str] = []
                while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    body.append(lines[j])
                    j += 1
                if django:
                    cols = [m.group(1) for b in body if (m := DJANGO_FIELD.match(b))]
                    out.append(Item(django.group(1).lower(), _columns(cols), i + 1))
                else:
                    named = next((m for b in body if (m := TABLENAME.match(b))), None)
                    if named:
                        cols = [m.group(1) for b in body if (m := SQLA_COLUMN.match(b))]
                        out.append(Item(named.group(1), _columns(cols), i + 1))
                i = j
            else:
                i += 1
        return out
    if path.endswith(".sql"):
        for m in CREATE_TABLE.finditer(text):
            end = text.find(");", m.end())
            block = text[m.end() : end if end != -1 else m.end() + 2000]
            cols = []
            for part in block.split("\n")[1:]:
                word = re.match(r"""\s*[`"\[]?(\w+)""", part)
                if word and word.group(1).lower() not in SQL_SKIP:
                    cols.append(word.group(1))
            out.append(Item(m.group(1), _columns(cols), text.count("\n", 0, m.start()) + 1))
        return out
    if path.endswith(".rb"):
        for i, ln in enumerate(lines):
            t = RAILS_TABLE.match(ln)
            if t:
                cols = []
                for nxt in lines[i + 1 :]:
                    if nxt.strip() == "end":
                        break
                    c = RAILS_COLUMN.match(nxt)
                    if c:
                        cols.append(c.group(1))
                out.append(Item(t.group(1), _columns(cols), i + 1))
        return out
    if path.endswith(".java"):
        for i, ln in enumerate(lines, 1):
            for m in JPA_TABLE.finditer(ln):
                out.append(Item(m.group(1), "table", i))
    return out


EXTRACTORS = {ENV: env_vars, CLI: commands, TABLES: tables}
LINE = re.compile(r"^(.+?)(?:: (.*?))? \(line (\d+)\)$")


def extract(kind: str, path: str, text: str) -> list[Item]:
    if TESTISH.search(path) or path.endswith((".md", ".rst", ".txt", ".mdx")):
        return []
    seen: set[str] = set()
    out = []
    for it in EXTRACTORS[kind](path, text):
        if it.name not in seen:
            seen.add(it.name)
            out.append(it)
    return out


def chunks(path: str, text: str) -> list[ParsedChunk]:
    out = []
    for kind in EXTRACTORS:
        found = extract(kind, path, text)
        if not found:
            continue
        body = "\n".join(f"{i.name}" + (f": {i.detail}" if i.detail else "") + f" (line {i.line})" for i in found)
        out.append(
            ParsedChunk(
                symbol=TITLES[kind],
                parent="",
                chunk_type=kind,
                start_line=found[0].line,
                end_line=found[-1].line,
                content=f"{TITLES[kind]} defined in {path}:\n{body}",
            )
        )
    return out


def parse(content: str) -> list[Item]:
    """The items written by `chunks`, read back."""
    out = []
    for ln in content.splitlines()[1:]:
        m = LINE.match(ln.strip())
        if m:
            out.append(Item(m.group(1), m.group(2) or "", int(m.group(3))))
    return out
