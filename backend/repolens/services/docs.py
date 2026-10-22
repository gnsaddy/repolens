"""Documentation parsing: heading-aware sections, outgoing links and code mentions.

For doc chunks we reuse the chunk columns: `symbol_name` = section heading, `parent_symbol` = heading path
("Install > Docker"), `imports` = outgoing links, `calls` = mentioned code identifiers / file paths.
"""

import re
from pathlib import PurePosixPath

from .parsing import ParsedChunk

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RST_UNDERLINE = re.compile(r"^([=\-~^\"#*+`])\1{2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_BACKTICK = re.compile(r"`([^`\n]{2,80})`")
_IDENT = re.compile(r"^[A-Za-z_][\w./:#\\-]*(\(\))?$")
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
MAX_SECTION_LINES = 80
WINDOW = 60
OVERLAP = 8


def slugify(heading: str) -> str:
    s = re.sub(r"[^\w\s-]", "", heading.lower()).strip()
    return re.sub(r"[\s]+", "-", s)


def extract_links(text: str) -> list[str]:
    out: list[str] = []
    for _, url in _LINK.findall(text):
        if url.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        if url not in out:
            out.append(url)
    for name in _WIKI.findall(text):
        w = "wiki:" + name.strip()
        if w not in out:
            out.append(w)
    return out[:60]


def extract_mentions(text: str) -> list[str]:
    """Identifiers and paths a doc refers to. Strong signal = backticks; weaker = camel/snake case in prose."""
    prose, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            prose.append(line)
    body = "\n".join(prose)
    out: list[str] = []

    def add(tok: str) -> None:
        tok = tok.strip().removesuffix("()")
        if tok and tok not in out:
            out.append(tok)

    for m in _BACKTICK.findall(body):
        if _IDENT.match(m.strip()):
            add(m)
    for m in _CAMEL.findall(body):
        add(m)
    for m in _SNAKE.findall(body):
        add(m)
    return out[:60]


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(line_index, level, title)] skipping fenced code; supports ATX (#) and RST underlines."""
    found: list[tuple[int, int, str]] = []
    fenced = False
    rst_levels: dict[str, int] = {}
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _ATX.match(line)
        if m:
            found.append((i, len(m.group(1)), m.group(2).strip()))
            continue
        if i + 1 < len(lines) and line.strip() and not line.startswith((" ", "\t")):
            u = _RST_UNDERLINE.match(lines[i + 1])
            if u and len(lines[i + 1].strip()) >= max(3, len(line.strip()) - 1):
                ch = u.group(1)
                found.append((i, rst_levels.setdefault(ch, len(rst_levels) + 1), line.strip()))
    return found


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """[(start, end)] of paragraphs, tables and fenced code blocks. A fence or a table is never cut in half."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        j = i
        if _FENCE.match(lines[i]):
            j = i + 1
            while j < n and not _FENCE.match(lines[j]):
                j += 1
            j = min(j + 1, n)
        elif lines[i].lstrip().startswith("|"):
            while j < n and lines[j].lstrip().startswith("|"):
                j += 1
        else:
            while j < n and lines[j].strip() and not _FENCE.match(lines[j]):
                j += 1
            while j - i > WINDOW // 2:  # one enormous paragraph: cut it by lines, with the usual overlap
                out.append((i, i + WINDOW // 2))
                i += WINDOW // 2
        out.append((i, j))
        i = j
    return out


def _split(body: list[str]) -> list[tuple[int, list[str]]]:
    """Cut a long section at paragraph boundaries into parts of about WINDOW lines, repeating the last paragraph."""
    if len(body) <= MAX_SECTION_LINES:
        return [(0, body)]
    parts: list[tuple[int, list[str]]] = []
    start = prev_start = prev_end = 0
    for b_start, b_end in _blocks(body):
        if b_end - start > WINDOW and b_start > start:  # this block would overflow: close the part before it
            parts.append((start, body[start:b_start]))
            last = (prev_start, prev_end)
            plain = not _FENCE.match(body[last[0]]) and not body[last[0]].lstrip().startswith("|")
            # repeat the end of the previous paragraph for context, but never part of a code block or table
            start = max(last[0], b_start - OVERLAP) if plain and last[1] == b_start else b_start
        prev_start, prev_end = b_start, b_end
    if start < len(body) and body[start:] and any(x.strip() for x in body[start:]):
        parts.append((start, body[start:]))
    return parts or [(0, body)]


def parse_markdown(path: str, text: str) -> list[ParsedChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    start = 0
    title_from_front = ""
    if lines[0].strip() == "---":  # YAML front matter
        for j in range(1, min(len(lines), 60)):
            if lines[j].strip() == "---":
                for fl in lines[1:j]:
                    if fl.lower().startswith("title:"):
                        title_from_front = fl.split(":", 1)[1].strip().strip("\"'")
                start = j + 1
                break
    heads = [h for h in _headings(lines) if h[0] >= start]
    stem = title_from_front or PurePosixPath(path).stem
    boundaries: list[tuple[int, int, str]] = []  # (line_index, level, title)
    if not heads or heads[0][0] > start:
        boundaries.append((start, 0, stem))
    boundaries.extend(heads)
    chunks: list[ParsedChunk] = []
    stack: list[tuple[int, str]] = []
    for n, (idx, level, title) in enumerate(boundaries):
        end = boundaries[n + 1][0] if n + 1 < len(boundaries) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = " > ".join(t for _, t in stack)
        if level:
            stack.append((level, title))
        body = lines[idx:end]
        if not any(line.strip() for line in body[1:] if level) and not any(line.strip() for line in body):
            continue
        for s, part in _split(body):
            content = "\n".join(part)
            chunks.append(
                ParsedChunk(
                    symbol=title,
                    parent=parent,
                    chunk_type="doc",
                    start_line=idx + s + 1,
                    end_line=idx + s + len(part),
                    content=content,
                    calls=extract_mentions(content),
                    imports=extract_links(content),
                )
            )
    return chunks
