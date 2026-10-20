"""Turn CI output into investigations: JUnit XML, pytest, Jest, Go test, or any log with stack traces.

Parsers are deliberately forgiving (CI logs are noisy) but never execute or fetch anything: input is text only.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from ..security import redact_stored

MAX_LOG = 2_000_000
MAX_FAILURES = 50
DETAIL_CAP = 6000
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class Failure:
    name: str
    kind: str  # junit | pytest | jest | go | trace
    message: str
    details: str
    file: str = ""
    line: int = 0
    count: int = 1

    def as_dict(self, index: int) -> dict:
        return {"index": index, **asdict(self)}


class CiLogError(ValueError):
    pass


def _first_line(text: str, cap: int = 300) -> str:
    for ln in text.splitlines():
        if ln.strip():
            return ln.strip()[:cap]
    return ""


# ---------------------------------------------------------------------------------------------------- JUnit XML
def parse_junit(text: str) -> list[Failure]:
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):  # no entity expansion tricks from untrusted files
        raise CiLogError("XML with a DOCTYPE or entities is not accepted")
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as e:
        raise CiLogError(f"Not valid XML: {e}") from None
    out = []
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        name = ".".join(x for x in (case.get("classname"), case.get("name")) if x) or "unnamed test"
        body = (bad.text or "").strip()
        msg = bad.get("message") or _first_line(body)
        file = case.get("file") or ""
        out.append(
            Failure(
                name,
                "junit",
                msg[:300],
                (msg + "\n" + body if msg and msg not in body else body)[:DETAIL_CAP],
                file,
                int(case.get("line") or 0),
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------- pytest
PYTEST_HEAD = re.compile(r"^_{3,} (.+?) _{3,}$")
PYTEST_FRAME = re.compile(r"^([\w./\\-]+\.py):(\d+): (\w+(?:\.\w+)*(?:Error|Exception|Failure)?.*)$")


def parse_pytest(text: str) -> list[Failure]:
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur: tuple[str, list[str]] | None = None
    for ln in lines:
        m = PYTEST_HEAD.match(ln)
        if m:
            cur = (m.group(1), [])
            sections.append(cur)
        elif re.match(r"^={3,} .* ={3,}$", ln) and cur is not None:
            cur = None
        elif cur is not None:
            cur[1].append(ln)
    out = []
    for name, body in sections:
        errs = [ln[1:].strip() for ln in body if ln.startswith("E ")]
        frame = next((PYTEST_FRAME.match(ln) for ln in reversed(body) if PYTEST_FRAME.match(ln)), None)
        out.append(
            Failure(
                name,
                "pytest",
                (errs[0] if errs else _first_line("\n".join(body)))[:300],
                "\n".join(body).strip()[:DETAIL_CAP],
                frame.group(1) if frame else "",
                int(frame.group(2)) if frame else 0,
            )
        )
    if not out:  # only the short summary was kept by the CI job
        for m in re.finditer(r"^(?:FAILED|ERROR) (\S+?)(?: - (.*))?$", text, re.M):
            file = m.group(1).split("::")[0]
            out.append(
                Failure(
                    m.group(1), "pytest", (m.group(2) or "test failed")[:300], (m.group(2) or "")[:DETAIL_CAP], file
                )
            )
    return out


# ---------------------------------------------------------------------------------------------------- Jest
def parse_jest(text: str) -> list[Failure]:
    out = []
    blocks = re.split(r"^\s*● ", text, flags=re.M)[1:]
    for b in blocks:
        head, _, rest = b.partition("\n")
        body = re.split(r"^\s*(?:Test Suites:|Tests:|Snapshots:)", rest, flags=re.M)[0].strip()
        frame = re.search(r"at .*?\(?([\w./\\@-]+\.(?:js|jsx|ts|tsx|mjs)):(\d+):\d+\)?", body)
        out.append(
            Failure(
                head.strip(),
                "jest",
                _first_line(body),
                body[:DETAIL_CAP],
                frame.group(1) if frame else "",
                int(frame.group(2)) if frame else 0,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------- Go
def parse_go(text: str) -> list[Failure]:
    out = []
    for m in re.finditer(r"^--- FAIL: (\S+).*\n((?:[ \t]+.*\n?)*)", text, re.M):
        body = m.group(2).strip()
        frame = re.search(r"([\w./-]+\.go):(\d+)", body)
        out.append(
            Failure(
                m.group(1),
                "go",
                _first_line(body) or "test failed",
                body[:DETAIL_CAP],
                frame.group(1) if frame else "",
                int(frame.group(2)) if frame else 0,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------- generic traces
TRACE_PY = re.compile(
    r"Traceback \(most recent call last\):\n(?:[ \t]+.*\n)+(\w[\w.]*(?:Error|Exception|Exit|Interrupt|Warning)?[^\n]*)",
    re.M,
)
TRACE_JS = re.compile(r"^((?:[A-Z]\w*)?(?:Error|Exception)[^\n]*)\n((?:[ \t]+at .*\n?)+)", re.M)
TRACE_JAVA = re.compile(
    r"^((?:Exception in thread \"[^\"]+\" )?[\w.$]+(?:Exception|Error)[^\n]*)\n((?:[ \t]+at .*\n?)+)", re.M
)


def parse_traces(text: str) -> list[Failure]:
    out = []
    for m in TRACE_PY.finditer(text):
        frames = re.findall(r'File "([^"]+)", line (\d+)', m.group(0))
        f, ln = frames[-1] if frames else ("", "0")
        out.append(
            Failure(
                m.group(1).split(":")[0].strip(), "trace", m.group(1).strip()[:300], m.group(0)[:DETAIL_CAP], f, int(ln)
            )
        )
    for rx in (TRACE_JS, TRACE_JAVA):
        for m in rx.finditer(text):
            frame = re.search(r"\(?([\w./\\@$-]+\.(?:js|jsx|ts|tsx|java|kt)):(\d+)", m.group(2))
            out.append(
                Failure(
                    m.group(1).split(":")[0].strip(),
                    "trace",
                    m.group(1).strip()[:300],
                    m.group(0)[:DETAIL_CAP],
                    frame.group(1) if frame else "",
                    int(frame.group(2)) if frame else 0,
                )
            )
    return out


def parse_ci_log(raw: str) -> tuple[str, list[Failure]]:
    """Detect the format and return (format, failures). Identical failures are merged with a count."""
    if len(raw) > MAX_LOG:
        raise CiLogError("Log is larger than 2 MB. Upload only the failing job's output.")
    text = ANSI.sub("", raw.replace("\r\n", "\n"))
    if not text.strip():
        raise CiLogError("The log is empty")
    head = text.lstrip()[:200]
    fmt, found = "trace", []
    if head.startswith("<?xml") or head.startswith("<testsuite"):
        fmt, found = "junit", parse_junit(text)
    else:
        for name, fn in (("pytest", parse_pytest), ("jest", parse_jest), ("go", parse_go), ("trace", parse_traces)):
            hits = fn(text)
            if hits:
                fmt, found = name, hits
                break
    merged: dict[tuple[str, str], Failure] = {}
    for f in found:
        key = (f.name, f.message)
        if key in merged:
            merged[key].count += 1
        else:
            merged[key] = f
    return fmt, list(merged.values())[:MAX_FAILURES]


def sanitize(f: Failure) -> Failure:
    """CI logs often contain tokens: redact before anything is stored or sent to a model."""
    f.message, _ = redact_stored(f.message)
    f.details, _ = redact_stored(f.details)
    return f


def to_query(f: Failure) -> str:
    where = f" ({f.file}:{f.line})" if f.file and f.line else f" ({f.file})" if f.file else ""
    return f"CI test failure: {f.name}{where}\n{f.message}\n\n{f.details}".strip()[:12000]
