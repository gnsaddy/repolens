"""Read a Jira ticket the way a senior engineer would: what is broken, what was tried, what is still unknown."""

import re
from collections import Counter
from datetime import UTC, datetime

from . import embeddings, retrieval
from .jira import JiraIssue

_ERROR = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Failure|Timeout|Panic)|[45]\d\d (?:[A-Z][a-z]+ ?)+)\b")
_PATH = re.compile(
    r"(?<![\w/])((?:[\w.-]+/)+[\w.-]+\.(?:py|js|jsx|ts|tsx|java|go|rb|rs|cs|php|kt|swift|scala|yml|yaml|sql))\b"
)
_TICK = re.compile(r"`([A-Za-z_][\w.]{2,80}(?:\(\))?)`")
_LINK = re.compile(r"https?://[^\s)>\]]+")
_PR = re.compile(r"/(?:pull|pull-requests|merge_requests|commit|commits)/[\w]+")
_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")

# comment intent cues, checked in this order
CUES: list[tuple[str, re.Pattern]] = [
    ("fix", re.compile(r"\b(fixed|fix(?:es)? (?:in|by)|merged|deployed|released|resolved|closing)\b", re.I)),
    ("cause", re.compile(r"\b(root cause|caused by|because|due to|turns out|the problem is|the issue is)\b", re.I)),
    ("workaround", re.compile(r"\b(work ?around|temporary|temporarily|hotfix|mitigat\w+|disabled|toggle)\b", re.I)),
    (
        "tried",
        re.compile(
            r"\b(tried|attempt\w*|restart\w*|roll(?:ed)? ?back|revert\w*|retr(?:y|ied)|cleared|redeploy\w*)\b", re.I
        ),
    ),
    (
        "repro",
        re.compile(r"\b(steps to reproduce|reproduc\w+|happens when|can ?not reproduce|only (?:on|when))\b", re.I),
    ),
]
_STOP = frozenset(
    "the a an and or of to in on for is are was were be it this that with from by at as not but have has had we you i "
    "they when what why how can could should would will issue ticket bug error".split()
)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) > 12]


def _kind(text: str) -> str:
    for name, rx in CUES:
        if rx.search(text):
            return name
    return "question" if "?" in text else "note"


def _age_days(iso: str) -> int | None:
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - d).days)
    except ValueError:
        return None


def analyze(issue: JiraIssue) -> dict:
    blobs = [issue.summary, issue.description, *issue.comments]
    text = "\n".join(b for b in blobs if b)
    errors = Counter(m.group(1).strip() for m in _ERROR.finditer(text))
    frames = retrieval.extract_stack_refs(text)
    files = sorted({m.group(1) for m in _PATH.finditer(text)} | {f["file"] for f in frames})
    symbols = [s for s, _ in Counter(m.group(1).removesuffix("()") for m in _TICK.finditer(text)).most_common(12)]
    links = _LINK.findall(text)
    prs = sorted({u for u in links if _PR.search(u)})
    related_keys = sorted(set(_KEY.findall(text)) - {issue.key})

    timeline = []
    claims: dict[str, list[dict]] = {k: [] for k in ("cause", "tried", "workaround", "fix", "repro")}
    questions: list[dict] = []
    for i, c in enumerate(issue.comment_items or [{"author": "", "created": "", "body": b} for b in issue.comments]):
        body = c.get("body", "")
        kind = _kind(body)
        timeline.append(
            {
                "author": c.get("author", ""),
                "created": c.get("created", ""),
                "kind": kind,
                "excerpt": re.sub(r"\s+", " ", body)[:240],
            }
        )
        for sent in _sentences(body):
            k = next((n for n, rx in CUES if rx.search(sent)), None)
            if k:
                claims[k].append({"by": c.get("author", ""), "text": sent[:280], "comment": i + 1})
        for sent in _sentences(body):
            if sent.endswith("?"):
                questions.append({"by": c.get("author", ""), "text": sent[:280], "comment": i + 1})
    # a question is "open" unless a later comment by someone else exists (a crude but useful signal)
    last = len(timeline)
    open_questions = [q for q in questions if q["comment"] == last or q["comment"] >= last - 1][-4:]

    words = Counter(
        embeddings.stem(w.lower())
        for w in embeddings.tokenize(issue.summary + " " + issue.description)
        if w.lower() not in _STOP and len(w) > 3
    )
    terms = list(
        dict.fromkeys([*errors, *symbols, *[Path_name(f) for f in files], *[w for w, _ in words.most_common(8)]])
    )[:14]

    updated_days = _age_days(issue.updated)
    participants = sorted({c["author"] for c in timeline if c["author"]})
    flags = []
    if not frames and not errors:
        flags.append("No error message or stack trace: ask for logs or an exact error.")
    if not claims["repro"] and not re.search(r"reproduc|steps", issue.description, re.I):
        flags.append("No reproduction steps are described.")
    if updated_days is not None and updated_days > 14 and issue.status.lower() not in ("done", "closed", "resolved"):
        flags.append(f"No activity for {updated_days} days.")
    if prs and not claims["fix"]:
        flags.append("A pull request is linked but nobody confirms it fixes the problem.")
    if claims["fix"] and issue.status.lower() not in ("done", "closed", "resolved"):
        flags.append("Comments mention a fix but the ticket is still open.")

    next_steps = []
    if frames:
        inner = next((f for f in frames if f.get("innermost")), frames[0])
        next_steps.append(f"Start at {inner['file']}:{inner['line']} (where the failure was raised).")
    if errors:
        next_steps.append(f"Search the code and docs for {', '.join(list(errors)[:3])}.")
    if claims["cause"]:
        next_steps.append("Verify the cause someone proposed in the comments against the code before acting on it.")
    if claims["workaround"]:
        next_steps.append("A workaround exists: decide whether it is safe to keep and track removing it.")
    next_steps.append("Run an investigation to rank likely causes against the indexed code, docs and pull requests.")

    headline = issue.summary
    if errors:
        headline += f" — signature: {', '.join(list(errors)[:2])}"
    return {
        "key": issue.key,
        "headline": headline,
        "status": issue.status,
        "priority": issue.priority,
        "reporter": issue.reporter,
        "assignee": issue.assignee,
        "participants": participants,
        "age_days": _age_days(issue.created),
        "updated_days": updated_days,
        "signals": {
            "errors": list(errors)[:8],
            "frames": frames[:8],
            "files": files[:12],
            "symbols": symbols,
            "pull_requests": prs[:6],
            "related_issues": related_keys[:8],
        },
        "discussion": {
            "comments": len(timeline),
            "cause": claims["cause"][:4],
            "tried": claims["tried"][:5],
            "workarounds": claims["workaround"][:3],
            "fixes": claims["fix"][:3],
            "open_questions": open_questions,
        },
        "timeline": timeline,
        "flags": flags,
        "search_terms": terms,
        "next_steps": next_steps,
    }


def Path_name(f: str) -> str:  # noqa: N802
    return f.rsplit("/", 1)[-1]


def investigation_query(issue: JiraIssue, digest: dict) -> str:
    """What an investigation should read: the ticket plus the distilled signals, so retrieval gets the right terms."""
    sig = digest["signals"]
    disc = digest["discussion"]
    parts = [issue.summary, issue.description]
    if sig["errors"]:
        parts.append("Errors: " + ", ".join(sig["errors"]))
    if sig["files"]:
        parts.append("Files mentioned: " + ", ".join(sig["files"][:8]))
    if sig["symbols"]:
        parts.append("Symbols mentioned: " + ", ".join(sig["symbols"][:8]))
    for label, key in (("Proposed cause", "cause"), ("Already tried", "tried")):
        if disc[key]:
            parts.append(f"{label}: " + " | ".join(x["text"] for x in disc[key][:3]))
    if issue.components:
        parts.append("Components: " + ", ".join(issue.components))
    return "\n\n".join(p for p in parts if p).strip()[:8000]
