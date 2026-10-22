"""What a senior engineer sees at a glance in a document: its kind, what it is for, and the facts worth knowing.

Everything here is deterministic and local, so it works without a language model and costs no tokens.
"""

import posixpath
import re

_FENCED = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)
_ENDPOINT = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w\-./{}:<>?=&%]*)")
_ENVVAR = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b")
_OWNER = re.compile(
    r"^\s*(?:\*\*)?(owner|maintainer|maintained by|team|on-?call|contact)(?:\*\*)?\s*[:\-]\s*(.+)$", re.I | re.M
)
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_ADMON = re.compile(r"^\s*>?\s*\**(warning|danger|caution|important|note)\**\s*[:!]\s*(.+)$", re.I | re.M)
_RISKY = re.compile(
    r"\b(never|do not|don't|must not|irreversible|destructive|data loss|drop table|rm -rf|force[- ]push)\b", re.I
)
_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\S", re.M)
_SHELL = {"bash", "sh", "shell", "zsh", "console", "terminal"}
_PROMPT = re.compile(r"^\s*\$\s*")

# (kind, cues in the file name, cues in the title, cues in the body)
_KINDS: list[tuple[str, re.Pattern, re.Pattern, re.Pattern]] = [
    (
        "runbook",
        re.compile(r"runbook|playbook|oncall|on-call|sop", re.I),
        re.compile(r"runbook|playbook|how to (?:restart|recover|rollback|fix)|incident response", re.I),
        re.compile(r"\b(rollback|restart|escalat\w+|mitigat\w+|page the|on-?call)\b", re.I),
    ),
    (
        "incident",
        re.compile(r"postmortem|post-mortem|incident|rca|outage", re.I),
        re.compile(r"postmortem|post-mortem|incident|root cause|outage", re.I),
        re.compile(r"\b(timeline|root cause|impact|action items|lessons learned)\b", re.I),
    ),
    (
        "decision",
        re.compile(r"adr|decision|rfc", re.I),
        re.compile(r"\b(adr|rfc|decision)\b", re.I),
        re.compile(r"\b(status:\s*(accepted|proposed|superseded)|consequences|alternatives considered)\b", re.I),
    ),
    (
        "api",
        re.compile(r"api|openapi|swagger|reference", re.I),
        re.compile(r"\bapi\b|endpoint|reference", re.I),
        re.compile(r"\b(GET|POST|PUT|DELETE|PATCH) /|status code|request body|response", re.I),
    ),
    (
        "design",
        re.compile(r"design|architecture|arch|overview", re.I),
        re.compile(r"design|architecture|overview", re.I),
        re.compile(r"\b(components?|data flow|trade-?offs?|diagram|sequence)\b", re.I),
    ),
    (
        "howto",
        re.compile(r"how-?to|guide|tutorial|setup|install|getting-?started|quickstart", re.I),
        re.compile(r"how to|guide|tutorial|set ?up|install|getting started|quick ?start", re.I),
        re.compile(r"\b(prerequisites?|step \d|first,|then,|next,)\b", re.I),
    ),
    (
        "changelog",
        re.compile(r"changelog|release-?notes|history|news", re.I),
        re.compile(r"changelog|release notes", re.I),
        re.compile(r"^\s*##\s*\[?v?\d+\.\d+", re.M),
    ),
    (
        "readme",
        re.compile(r"^readme", re.I),
        re.compile(r"^readme$", re.I),
        re.compile(r"\b(installation|usage|license|contributing)\b", re.I),
    ),
]
KIND_LABEL = {
    "runbook": "Runbook",
    "incident": "Incident write-up",
    "decision": "Decision record",
    "api": "API reference",
    "design": "Design / architecture",
    "howto": "How-to guide",
    "changelog": "Changelog",
    "readme": "README",
    "general": "Document",
}


def classify(path: str, title: str, text: str) -> str:
    """The most likely kind of document. File name counts most, then title, then what the body talks about."""
    name = posixpath.basename(path)
    best, score = "general", 0
    body = text[:6000]
    for kind, name_rx, title_rx, body_rx in _KINDS:
        s = 3 * bool(name_rx.search(name) or name_rx.search(posixpath.dirname(path))) + 2 * bool(title_rx.search(title))
        s += min(len(body_rx.findall(body)), 3)
        if s > score:
            best, score = kind, s
    return best if score >= 3 else "general"


def _commands(text: str) -> list[str]:
    out: list[str] = []
    for lang, body in _FENCED.findall(text):
        if lang.lower() in _SHELL or (not lang and re.search(r"^\s*\$ ", body, re.M)):
            for ln in body.splitlines():
                ln = _PROMPT.sub("", ln).strip()
                if ln and not ln.startswith("#") and ln not in out:
                    out.append(ln[:160])
    return out[:12]


def _first_paragraph(text: str) -> str:
    body = _FENCED.sub("", text)
    for block in re.split(r"\n\s*\n", body):
        lines = [
            ln
            for ln in block.splitlines()
            if ln.strip() and not ln.lstrip().startswith(("#", "|", "---", "```", "<", "!["))
        ]
        para = " ".join(lines).strip()
        para = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", para)
        if len(para) > 40 and not para.lower().startswith(("table of contents", "toc")):
            return para[:320] + ("…" if len(para) > 320 else "")
    return ""


def insights(path: str, title: str, text: str) -> dict:
    """Kind, purpose, and the facts an engineer would look for: commands, endpoints, settings, owners, warnings."""
    kind = classify(path, title, text)
    prose = _FENCED.sub("", text)
    steps = len(_STEP.findall(prose)) if kind in ("runbook", "howto") else 0
    warnings = [{"level": m.group(1).lower(), "text": m.group(2).strip()[:220]} for m in _ADMON.finditer(prose)][:6]
    if kind in ("runbook", "howto"):
        for ln in prose.splitlines():
            if _RISKY.search(ln) and len(ln.strip()) > 20 and not any(w["text"] in ln for w in warnings):
                warnings.append({"level": "caution", "text": ln.strip().lstrip("-*> ")[:220]})
                if len(warnings) >= 6:
                    break
    owners = [{"role": m.group(1).lower(), "who": m.group(2).strip()[:80]} for m in _OWNER.finditer(prose)][:4]
    dates = sorted(set(_DATE.findall(text)))
    return {
        "kind": kind,
        "kind_label": KIND_LABEL[kind],
        "purpose": _first_paragraph(text),
        "commands": _commands(text),
        "endpoints": [f"{m} {p}" for m, p in dict.fromkeys(_ENDPOINT.findall(text))][:10],
        "settings": sorted(set(_ENVVAR.findall(prose)))[:12],
        "owners": owners,
        "warnings": warnings,
        "steps": steps,
        "last_dated": dates[-1] if dates else None,
        "words": len(prose.split()),
        "reading_minutes": max(1, round(len(prose.split()) / 220)),
    }
