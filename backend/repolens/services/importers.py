"""Turn files people bring from their laptop into Markdown documents: Word files, PDFs, Notion exports and zips of documents.

Everything here reads files someone else made, so it is deliberately strict: sizes and counts are capped, zip entries may not
escape their folder, XML that declares entities is refused (they can be used to blow up memory), and a file that cannot be read
is skipped with a plain reason instead of failing the whole upload. No model and no network are involved, and nothing here
reads text out of pictures (OCR): a scanned PDF is reported as having no selectable text."""

import csv
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

TEXT_EXT = (".md", ".mdx", ".rst", ".txt", ".adoc")
IMPORT_EXT = (*TEXT_EXT, ".docx", ".pdf", ".zip", ".csv")

MAX_FILE = 25 * 1024 * 1024  # one file, as received
MAX_ENTRY = 8 * 1024 * 1024  # one file inside a zip, unpacked
MAX_ZIP_TOTAL = 60 * 1024 * 1024  # everything in a zip, unpacked
MAX_ZIP_ENTRIES = 1500
MAX_TEXT = 1_000_000  # characters kept from one document
MAX_PDF_PAGES = 400
MAX_TABLE_ROWS = 300
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NOTION_ID = re.compile(r"[ _-][0-9a-f]{32}(?=\.|/|$)", re.I)  # Notion adds " <32 hex characters>" to every name


@dataclass
class Doc:
    path: str
    content: str
    kind: str  # text | docx | pdf | notion | csv
    source: str  # the file it came from


@dataclass
class Skip:
    name: str
    reason: str


class ImportError_(Exception):
    """A file that cannot be imported, with a reason a person can act on."""


# ------------------------------------------------------------------------------------------------ helpers
def _safe_xml(data: bytes) -> ET.Element:
    head = data[:4096].upper()
    if b"<!ENTITY" in data.upper() or b"<!DOCTYPE" in head:
        raise ImportError_("it contains XML declarations that are not allowed")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ImportError_("its content is not valid") from e


def _decode(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise ImportError_("it looks like a binary file, not text")
    return data.decode("utf-8", errors="replace")


def _cap(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return (
        text
        if len(text) <= MAX_TEXT
        else text[:MAX_TEXT] + "\n\n_(cut here: the document is longer than the import limit)_\n"
    )


def _clean_segment(seg: str) -> str:
    return _NOTION_ID.sub("", seg).strip()


def _stem(name: str) -> str:
    base = posixpath.basename(name.replace("\\", "/"))
    return _clean_segment(posixpath.splitext(base)[0]) or "document"


def _table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = min(max(len(r) for r in rows), 20)
    norm = [
        ([c.replace("|", "\\|").replace("\n", " ").strip() for c in r] + [""] * width)[:width]
        for r in rows[: MAX_TABLE_ROWS + 1]
    ]
    out = ["| " + " | ".join(norm[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    if len(rows) > MAX_TABLE_ROWS + 1:
        out.append(f"\n_({len(rows) - MAX_TABLE_ROWS - 1} more rows not imported)_")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ Word
def _para_text(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            parts.append(el.text)
        elif el.tag == W + "tab":
            parts.append(" ")
        elif el.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def docx_to_markdown(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        info = zf.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ImportError_("it is not a Word (.docx) document") from e
    if info.file_size > MAX_ENTRY * 2:
        raise ImportError_("the document is too large to import")
    root = _safe_xml(zf.read(info))
    body = root.find(W + "body")
    if body is None:
        raise ImportError_("it has no content")
    lines: list[str] = []
    for el in body:
        if el.tag == W + "p":
            text = _para_text(el)
            if not text:
                lines.append("")
                continue
            style = ""
            ppr = el.find(W + "pPr")
            if ppr is not None:
                ps = ppr.find(W + "pStyle")
                if ps is not None:
                    style = (ps.get(W + "val") or "").lower().replace(" ", "")
                num = ppr.find(W + "numPr")
            else:
                num = None
            m = re.fullmatch(r"heading([1-6])", style)
            if style == "title":
                lines.append(f"# {text}")
            elif m:
                lines.append(f"{'#' * int(m.group(1))} {text}")
            elif num is not None or style.startswith("listparagraph"):
                lvl = 0
                if num is not None:
                    il = num.find(W + "ilvl")
                    lvl = int(il.get(W + "val") or 0) if il is not None and (il.get(W + "val") or "0").isdigit() else 0
                lines.append("  " * min(lvl, 6) + f"- {text}")
            else:
                lines.append(text)
        elif el.tag == W + "tbl":
            rows = [
                [" ".join(filter(None, (_para_text(p) for p in tc.iter(W + "p")))) for tc in tr.findall(W + "tc")]
                for tr in el.findall(W + "tr")
            ]
            lines += ["", _table(rows), ""]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        raise ImportError_("it has no text")
    return text + "\n"


# ------------------------------------------------------------------------------------------------ PDF
def pdf_to_markdown(data: bytes, title: str) -> str:
    """The text of a PDF that has selectable text, read with `pypdf`. No OCR."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError_("PDF import is not available on this server (the `pypdf` package is missing)") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ImportError_("it is password protected")
        n = len(reader.pages)
        sections = []
        for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], 1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(f"## Page {i}\n\n{text}")
    except ImportError_:
        raise
    except Exception as e:  # a damaged or unusual file: say so instead of failing the upload
        raise ImportError_("it could not be read as a PDF") from e
    body = "\n\n".join(sections)
    if len(body.strip()) < 20:
        raise ImportError_(
            "it has no selectable text; it looks like a scanned document, and reading text out of pictures (OCR) is not supported"
        )
    note = f"\n\n_(only the first {MAX_PDF_PAGES} of {n} pages were imported)_" if n > MAX_PDF_PAGES else ""
    return f"# {title}\n\n{body}{note}\n"


# ------------------------------------------------------------------------------------------------ zip (Notion export or any folder of documents)
def _zip_name(raw: str) -> str | None:
    """A safe relative path inside a zip, or None for entries to skip (folders, hidden files, escapes)."""
    name = raw.replace("\\", "/").lstrip("/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None
    return "/".join(parts)


def _read_zip(data: bytes, depth: int, budget: list[int]) -> list[tuple[str, bytes]]:
    """(path, bytes) of the files in a zip, one nested zip deep, within the size and count limits."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ImportError_("it is not a valid zip file") from e
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ImportError_(f"it has more than {MAX_ZIP_ENTRIES} files; split it into smaller zips")
    out: list[tuple[str, bytes]] = []
    for info in infos:
        name = _zip_name(info.filename)
        if name is None or info.flag_bits & 0x1:  # unsafe or encrypted entries are skipped
            continue
        low = name.lower()
        if not low.endswith((*TEXT_EXT, ".csv", ".zip", ".markdown")):
            continue  # images, attachments and web pages are not imported
        if info.file_size > MAX_ENTRY:
            continue
        with zf.open(info) as fh:
            blob = fh.read(MAX_ENTRY + 1)
        budget[0] += len(blob)
        if len(blob) > MAX_ENTRY or budget[0] > MAX_ZIP_TOTAL:
            raise ImportError_("it unpacks to more than the import limit; split it into smaller zips")
        if low.endswith(".zip"):
            if depth < 1:
                out += [(f"{name}/{p}", b) for p, b in _read_zip(blob, depth + 1, budget)]
            continue
        out.append((name, blob))
    return out


def _rewrite_links(content: str, old_dir: str, mapping: dict[str, str], new_path: str) -> str:
    """Notion links point at the long names with ids; point them at the cleaned names we saved."""
    new_dir = posixpath.dirname(new_path)

    def fix(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", target, re.I):
            return m.group(0)
        old = posixpath.normpath(posixpath.join(old_dir, unquote(target.split("#")[0])))
        new = mapping.get(old)
        if not new:
            return m.group(0)
        return f"[{label}]({quote(posixpath.relpath(new, new_dir or '.'))})"

    return re.sub(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)", fix, content)


def import_zip(name: str, data: bytes) -> tuple[list[Doc], list[Skip]]:
    files = _read_zip(data, 0, [0])
    docs: list[Doc] = []
    skips: list[Skip] = []
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    entries: list[tuple[str, str, str, bytes]] = []  # (old path, new path, kind, bytes)
    for old, blob in files:
        low = old.lower()
        if low.endswith("_all.csv") and _NOTION_ID.search(old):
            continue  # Notion writes every database twice; the plain copy is enough
        new = "/".join(_clean_segment(s) for s in old.split("/"))
        base, ext = posixpath.splitext(new)
        if ext.lower() == ".csv":
            new, ext = base + ".md", ".md"  # a database becomes a page with a table
        elif ext.lower() == ".markdown":
            new, ext = base + ".md", ".md"
        cand, n = new, 1
        while cand.lower() in taken:
            n += 1
            cand = f"{base} ({n}){ext}"
        taken.add(cand.lower())
        mapping[old] = cand
        entries.append(
            (old, cand, "csv" if low.endswith(".csv") else "notion" if _NOTION_ID.search(old) else "text", blob)
        )
    for old, new, kind, blob in entries:
        try:
            if kind == "csv":
                rows = list(csv.reader(io.StringIO(_decode(blob), newline="")))
                table = _table(rows)
                if not table:
                    raise ImportError_("it is empty")
                text = f"# {_stem(new)}\n\n{table}\n"
            else:
                text = _rewrite_links(_decode(blob), posixpath.dirname(old), mapping, new)
            docs.append(Doc(new, _cap(text), kind, f"{name}:{old}"))
        except (ImportError_, csv.Error) as e:
            skips.append(Skip(f"{name}:{old}", str(e)))
    if not docs and not skips:
        skips.append(Skip(name, "it has no documents (Markdown, text or CSV files) to import"))
    return docs, skips


# ------------------------------------------------------------------------------------------------ entry point
def convert(name: str, data: bytes) -> tuple[list[Doc], list[Skip]]:
    """Documents (and reasons for anything skipped) from one uploaded file."""
    base = posixpath.basename(name.replace("\\", "/"))
    low = base.lower()
    if len(data) > MAX_FILE:
        return [], [Skip(name, f"it is larger than {MAX_FILE // (1024 * 1024)} MB")]
    try:
        if low.endswith(".zip"):
            return import_zip(base, data)
        if low.endswith(".docx"):
            return [
                Doc(f"{_stem(base)}.md", _cap(f"> Imported from {base}\n\n{docx_to_markdown(data)}"), "docx", base)
            ], []
        if low.endswith(".pdf"):
            return [
                Doc(
                    f"{_stem(base)}.md",
                    _cap(f"> Imported from {base}\n\n{pdf_to_markdown(data, _stem(base))}"),
                    "pdf",
                    base,
                )
            ], []
        if low.endswith(".csv"):
            table = _table(list(csv.reader(io.StringIO(_decode(data), newline=""))))
            if not table:
                raise ImportError_("it is empty")
            return [Doc(f"{_stem(base)}.md", _cap(f"# {_stem(base)}\n\n{table}\n"), "csv", base)], []
        if low.endswith(TEXT_EXT):
            return [
                Doc(_clean_segment(base) if _NOTION_ID.search(base) else base, _cap(_decode(data)), "text", base)
            ], []
        return [], [Skip(name, f"this file type is not supported (use {', '.join(IMPORT_EXT)})")]
    except (ImportError_, csv.Error) as e:
        return [], [Skip(name, str(e))]
