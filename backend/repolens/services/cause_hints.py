"""Read an error message against the retrieved code, the way an engineer scans for the line that can raise it.

No model is involved: the error text names what was missing (a key, an attribute, an index), and the code that
reads it, or builds the object that lacks it, is usually in the evidence already.
"""

import re

_KEYERR = re.compile(r"KeyError:?\s*['\"]?([\w.\-]+)['\"]?")

_NONE_ATTR = re.compile(r"'NoneType' object has no attribute '(\w+)'")
_JS_READ = re.compile(r"Cannot read propert(?:y|ies) of (?:undefined|null) \(reading '(\w+)'\)")
_INDEX = re.compile(r"IndexError: (?:list|tuple|string) index out of range")
_ZERO = re.compile(r"ZeroDivisionError")
_NPE = re.compile(r"NullPointerException")


def _lines(evidence: list[dict]) -> list[dict]:
    out = []
    for e in evidence:
        if e.get("type") != "code":
            continue
        for i, text in enumerate((e.get("snippet") or "").splitlines()):
            out.append({"file": e.get("file", ""), "line": int(e.get("start_line", 1)) + i, "text": text.rstrip()})
    return out


def _site(x: dict) -> dict:
    return {"file": x["file"], "line": x["line"], "text": x["text"].strip()[:140]}


def hint(query: str, evidence: list[dict]) -> dict | None:
    """A concrete explanation tied to code lines, or None when the error is not one we can read."""
    code = _lines(evidence)
    if not code:
        return None
    if m := _KEYERR.search(query):
        key = m.group(1)
        read = [x for x in code if re.search(rf"\[\s*['\"]{re.escape(key)}['\"]\s*\]", x["text"])]
        built = [
            x
            for x in code
            if re.search(r"\{\s*['\"]\w+['\"]\s*:", x["text"])
            and not re.search(rf"['\"]{re.escape(key)}['\"]", x["text"])
        ]
        if read:
            site = read[0]
            why = f'`{site["file"]}:{site["line"]}` reads `["{key}"]` directly, so any record without that key raises KeyError.'
            if built:
                why += (
                    f" `{built[0]['file']}:{built[0]['line']}` builds records without `{key}`, "
                    "so this input reaches the read."
                )
            return {
                "error": "KeyError",
                "detail": key,
                "summary": f"KeyError '{key}': the code reads that key unguarded at {site['file']}:{site['line']}.",
                "explanation": why,
                "fix": f'Use `.get("{key}")` with a default, or make the producer always set `{key}`; add a test for a record without it.',
                "sites": [_site(site), *[_site(b) for b in built[:2]]],
            }
    if m := _NONE_ATTR.search(query) or _JS_READ.search(query):
        attr = m.group(1)
        use = [x for x in code if re.search(rf"\.{re.escape(attr)}\b", x["text"])]
        ret_none = [x for x in code if re.search(r"\breturn\s+None\b|\breturn\s*;|\bnull\b", x["text"])]
        if use:
            site = use[0]
            why = f"`{site['file']}:{site['line']}` uses `.{attr}` on a value that was None/undefined."
            if ret_none:
                why += f" `{ret_none[0]['file']}:{ret_none[0]['line']}` can return nothing, which is the likely source."
            return {
                "error": "NoneType",
                "detail": attr,
                "summary": f"A missing value reaches `.{attr}` at {site['file']}:{site['line']}.",
                "explanation": why,
                "fix": "Check for None before using the attribute, or make the producer return a value or raise a clear error.",
                "sites": [_site(site), *[_site(r) for r in ret_none[:2]]],
            }
    if _INDEX.search(query):
        use = [x for x in code if re.search(r"\w\[\s*(?:-?\d+|\w+)\s*\]", x["text"])]
        if use:
            s = use[0]
            return {
                "error": "IndexError",
                "detail": "",
                "summary": f"An index into a possibly empty sequence at {s['file']}:{s['line']}.",
                "explanation": f"`{s['file']}:{s['line']}` indexes a sequence without checking its length.",
                "fix": "Guard for an empty sequence before indexing, and test the empty case.",
                "sites": [_site(s)],
            }
    if _ZERO.search(query):
        use = [x for x in code if re.search(r"[^/]/[^/=]", x["text"]) or "%" in x["text"]]
        if use:
            s = use[0]
            return {
                "error": "ZeroDivisionError",
                "detail": "",
                "summary": f"A division whose divisor can be zero at {s['file']}:{s['line']}.",
                "explanation": f"`{s['file']}:{s['line']}` divides without checking the divisor.",
                "fix": "Handle a zero divisor explicitly (skip, default or raise a clear error) and test it.",
                "sites": [_site(s)],
            }
    if _NPE.search(query):
        return None
    return None
