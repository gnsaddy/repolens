"""Fold new changelog entries into the current version's file and rebuild the index: python changelog/build_index.py
(or: make changelog-index).

A new entry starts as a small file in changelog/unreleased/ (make changelog title="..."). This script moves each one into
changelog/<version>.md under its date (newest first), deletes the small file, and rewrites README.md's list of versions.
The version is VERSION_LABEL in backend/repolens/__init__.py, so a new release starts a new file by itself."""

import re
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
UNRELEASED = HERE / "unreleased"
START, END = "<!-- versions:start -->", "<!-- versions:end -->"
DATE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
ENTRY = re.compile(r"^### (.+?)\s*$")

INTRO = """# Changelog

One file per release, named after the version (for example `v0.2-beta.md`). Inside, entries are grouped by date, newest first,
and each entry lists what changed under **Added**, **Changed**, **Fixed**, **Security** and **Upgrade notes**. Write for the person
who runs or uses RepoLens, not for the person who wrote the code: what changed, why it matters, and anything they must do.

**Adding an entry:** `make changelog title="short title"` creates a small draft in `unreleased/` from `TEMPLATE.md`; fill it in
(leave out the empty sections), then run `make changelog-index`. That folds the draft into the current version's file, deletes
the draft and rebuilds the list below. Update `docs/features.md`, `docs/architecture.md` and `SECURITY.md` in the same change
when behaviour, design or protections move. Entries are added in the same commit as the change.

"""


def version_label() -> str:
    text = (ROOT / "backend" / "repolens" / "__init__.py").read_text(encoding="utf8")
    m = re.search(r'^VERSION_LABEL\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("VERSION_LABEL not found in backend/repolens/__init__.py")
    return m.group(1)


def read_draft(path: Path) -> tuple[str, str]:
    """(title, body) of a draft: the first '# ' line is the title; headings below it move down one level."""
    title, body = path.stem, []
    seen = False
    for line in path.read_text(encoding="utf8").splitlines():
        if not seen and line.startswith("# "):
            title, seen = line[2:].strip(), True
            continue
        body.append("#" + line if line.startswith("#") else line)
    return title, "\n".join(body).strip()


def parse(text: str) -> dict[str, list[tuple[str, str]]]:
    """A version file as {date: [(title, body), ...]} in file order."""
    days: dict[str, list[tuple[str, str]]] = {}
    date: str | None = None
    entry: list[str] | None = None
    title = ""

    def close() -> None:
        if date and entry is not None:
            days.setdefault(date, []).append((title, "\n".join(entry).strip()))

    for line in text.splitlines():
        if m := DATE.match(line):
            close()
            date, entry = m.group(1), None
        elif (m := ENTRY.match(line)) and date:
            close()
            title, entry = m.group(1), []
        elif entry is not None:
            entry.append(line)
    close()
    return days


def render(label: str, days: dict[str, list[tuple[str, str]]]) -> str:
    out = [f"# {label}", ""]
    for date in sorted(days, reverse=True):
        out += [f"## {date}", ""]
        for title, body in days[date]:
            out += [f"### {title}", "", body, ""]
    return "\n".join(out).rstrip() + "\n"


def fold() -> tuple[str, int]:
    """Move every draft in unreleased/ into the current version's file. Returns (version label, drafts folded)."""
    label = version_label()
    target = HERE / f"{label}.md"
    days = parse(target.read_text(encoding="utf8")) if target.exists() else {}
    drafts = sorted(UNRELEASED.glob("20??-??-??-*.md"), reverse=True) if UNRELEASED.exists() else []
    fresh: dict[str, list[tuple[str, str]]] = {}
    for p in drafts:
        fresh.setdefault(p.name[:10], []).append(read_draft(p))
    for date, entries in fresh.items():
        days[date] = entries + days.get(date, [])  # the newest entries lead their day
    if drafts:
        target.write_text(render(label, days), encoding="utf8", newline="\n")
        for p in drafts:
            p.unlink()
    return label, len(drafts)


def write_index() -> int:
    rows = []
    for p in sorted(HERE.glob("v*.md"), key=lambda p: [int(n) for n in re.findall(r"\d+", p.stem)], reverse=True):
        days = parse(p.read_text(encoding="utf8"))
        count = sum(len(v) for v in days.values())
        span = f"{min(days)} to {max(days)}" if days else "no entries yet"
        rows.append(f"- [{p.stem}]({p.name}) · {count} {'entry' if count == 1 else 'entries'} · {span}")
    pending = len(list(UNRELEASED.glob("20??-??-??-*.md"))) if UNRELEASED.exists() else 0
    note = f"\n\n{pending} draft(s) in `unreleased/` not folded yet: run `make changelog-index`." if pending else ""
    block = f"{START}\n" + "\n".join(rows) + f"\n{END}{note}\n"
    (HERE / "README.md").write_text(INTRO + "## Versions\n\n" + block, encoding="utf8", newline="\n")
    return len(rows)


def main() -> None:
    label, folded = fold()
    versions = write_index()
    print(f"{folded} entr{'y' if folded == 1 else 'ies'} folded into {label}.md; {versions} version file(s)")


if __name__ == "__main__":
    main()
