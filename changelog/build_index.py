"""Rebuild changelog/README.md's list from the entry files: python changelog/build_index.py  (or: make changelog-index)"""

import re
from pathlib import Path

HERE = Path(__file__).parent
START, END = "<!-- entries:start -->", "<!-- entries:end -->"


def title_of(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def main() -> None:
    entries = sorted((p for p in HERE.glob("20??-??-??-*.md")), reverse=True)
    lines = [f"- {p.name[:10]} · [{title_of(p)}]({p.name})" for p in entries]
    readme = HERE / "README.md"
    text = readme.read_text()
    block = f"{START}\n" + "\n".join(lines) + f"\n{END}"
    text = re.sub(f"{re.escape(START)}.*?{re.escape(END)}", lambda _: block, text, flags=re.S)
    readme.write_text(text)
    print(f"{len(entries)} entries")


if __name__ == "__main__":
    main()
