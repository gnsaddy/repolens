"""Who knows this code? Ownership from commit history, with recency and size weighting.

For every file, each author earns a score from the commits that touched it: bigger changes count more (log-scaled so
a one-off reformat does not dominate) and old work fades with a half-life, so the people who maintain the code *now*
rank above those who wrote it years ago. Scores are aggregated for directories. This is a hint about who to ask, not
an assessment of anyone: it only sees commits that were indexed, and a commit author is not always the person who
understands the code.
"""

import math
import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Change, Repository

HALF_LIFE_DAYS = 180.0
STALE_DAYS = 365
BUS_FACTOR_SHARE = 0.8
BOT = re.compile(r"(\[bot\]|dependabot|renovate|github-actions|^bot$)", re.I)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _weight(adds: int, dels: int) -> float:
    return 1.0 + math.log1p(max(adds + dels, 0))


class Table:
    """author scores per (file path), built from the repository's commits."""

    def __init__(self, db: Session, repo_id: str, now: datetime | None = None):
        self.now = now or datetime.now(UTC)
        self.files: dict[str, dict[str, dict]] = defaultdict(dict)
        for ch in db.scalars(
            select(Change).where(Change.repo_id == repo_id).order_by(Change.committed_at.desc()).limit(5000)
        ):
            author = (ch.author or "").strip()
            if not author or BOT.search(author):
                continue
            when = _aware(ch.committed_at)
            decay = 0.5 ** (max((self.now - when).days, 0) / HALF_LIFE_DAYS)
            for f in ch.files or []:
                row = self.files[f.get("path", "")].setdefault(
                    author, {"score": 0.0, "commits": 0, "lines": 0, "last": when}
                )
                row["score"] += _weight(f.get("additions", 0), f.get("deletions", 0)) * decay
                row["commits"] += 1
                row["lines"] += f.get("additions", 0) + f.get("deletions", 0)
                row["last"] = max(row["last"], when)

    def under(self, prefix: str) -> dict[str, dict]:
        """Aggregate authors over a file or a directory."""
        prefix = prefix.strip("/")
        out: dict[str, dict] = {}
        for path, authors in self.files.items():
            if prefix and path != prefix and not path.startswith(prefix + "/"):
                continue
            for a, v in authors.items():
                o = out.setdefault(a, {"score": 0.0, "commits": 0, "lines": 0, "last": v["last"]})
                o["score"] += v["score"]
                o["commits"] += v["commits"]
                o["lines"] += v["lines"]
                o["last"] = max(o["last"], v["last"])
        return out


def _ranked(authors: dict[str, dict], limit: int) -> list[dict]:
    total = sum(v["score"] for v in authors.values()) or 1.0
    rows = [
        {
            "author": a,
            "share": round(v["score"] / total, 3),
            "commits": v["commits"],
            "lines": v["lines"],
            "last_commit_at": v["last"],
        }
        for a, v in authors.items()
    ]
    return sorted(rows, key=lambda r: (-r["share"], r["author"]))[:limit]


def bus_factor(authors: dict[str, dict]) -> int:
    """How many people together account for 80% of the knowledge. 1 means one person holds almost all of it."""
    total = sum(v["score"] for v in authors.values())
    if total <= 0:
        return 0
    acc, n = 0.0, 0
    for v in sorted(authors.values(), key=lambda v: -v["score"]):
        acc += v["score"]
        n += 1
        if acc / total >= BUS_FACTOR_SHARE:
            break
    return n


def file_owners(db: Session, repo_id: str, path: str, limit: int = 3, now: datetime | None = None) -> dict:
    t = Table(db, repo_id, now)
    authors = t.under(path)
    return {"path": path, "owners": _ranked(authors, limit), "bus_factor": bus_factor(authors)}


def _area(path: str) -> str:
    head = path.split("/", 1)
    return head[0] if len(head) > 1 else "(root files)"


def overview(db: Session, repo: Repository, now: datetime | None = None) -> dict:
    t = Table(db, repo.id, now)
    now = t.now
    areas: dict[str, dict[str, dict]] = defaultdict(dict)
    for path, authors in t.files.items():
        for a, v in authors.items():
            o = areas[_area(path)].setdefault(a, {"score": 0.0, "commits": 0, "lines": 0, "last": v["last"]})
            o["score"] += v["score"]
            o["commits"] += v["commits"]
            o["lines"] += v["lines"]
            o["last"] = max(o["last"], v["last"])
    rows = []
    for name, authors in areas.items():
        owners = _ranked(authors, 3)
        last = max(v["last"] for v in authors.values())
        bf = bus_factor(authors)
        stale = (now - _aware(owners[0]["last_commit_at"])).days > STALE_DAYS if owners else False
        rows.append(
            {
                "area": name,
                "owners": owners,
                "bus_factor": bf,
                "commits": sum(v["commits"] for v in authors.values()),
                "last_activity": last,
                "risk": "stale_owner" if stale else "single_owner" if bf == 1 and len(authors) >= 1 else "ok",
            }
        )
    rows.sort(key=lambda r: (-r["commits"], r["area"]))
    everything = t.under("")
    people = []
    for a, v in everything.items():
        mine = sorted(((ar, av[a]["score"]) for ar, av in areas.items() if a in av), key=lambda x: -x[1])
        people.append(
            {
                "author": a,
                "share": round(v["score"] / (sum(x["score"] for x in everything.values()) or 1), 3),
                "commits": v["commits"],
                "last_commit_at": v["last"],
                "areas": [x[0] for x in mine[:3]],
            }
        )
    people.sort(key=lambda r: (-r["share"], r["author"]))
    return {
        "repo_id": repo.id,
        "repo": repo.name,
        "summary": {
            "people": len(everything),
            "areas": len(rows),
            "bus_factor": bus_factor(everything),
            "areas_at_risk": sum(1 for r in rows if r["risk"] != "ok"),
        },
        "areas": rows,
        "people": people[:25],
    }


def experts(db: Session, repo_id: str, paths: list[str], limit: int = 3, now: datetime | None = None) -> list[dict]:
    """People to ask about a set of files (e.g. the suspects of an investigation)."""
    t = Table(db, repo_id, now)
    merged: dict[str, dict] = {}
    for p in dict.fromkeys(paths):
        for a, v in t.under(p).items():
            o = merged.setdefault(a, {"score": 0.0, "commits": 0, "lines": 0, "last": v["last"], "files": set()})
            o["score"] += v["score"]
            o["commits"] += v["commits"]
            o["lines"] += v["lines"]
            o["last"] = max(o["last"], v["last"])
            o["files"].add(p)
    rows = _ranked(merged, limit)
    for r in rows:
        r["files"] = sorted(merged[r["author"]]["files"])
    return rows
