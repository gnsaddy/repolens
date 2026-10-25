"""A short, reviewable summary of a pull request, built from what RepoLens already knows: how risky the change is and why, who
knows the code, which tests and documents relate to it, and what depends on it. Posted as a comment by the GitHub Action."""

from sqlalchemy.orm import Session

from ..models import Repository
from . import ownership
from . import risk as oracle
from .access import Scope

MARKER = "<!-- repolens-pr-report -->"
LEVEL = {"low": "Low risk", "medium": "Medium risk", "high": "High risk", "critical": "Critical risk"}


def _code(xs: list[str], n: int) -> str:
    shown = ", ".join(f"`{x}`" for x in xs[:n])
    return shown + (f" and {len(xs) - n} more" if len(xs) > n else "")


def build(
    db: Session,
    repo: Repository,
    scope: Scope,
    files: list[dict],
    title: str = "",
    pr_number: int | None = None,
    app_url: str = "",
    full: bool = False,
) -> dict:
    """`full=False` keeps the comment to this repository only: dependents and documents that live in other repositories are left
    out, because the comment lands on a pull request that may be public."""
    ci = oracle.from_files(repo, files, title)
    r = oracle.assess(db, ci, scope)
    aff = r["affected"]
    same = lambda items: [x for x in items if full or x.get("repo_id") in (None, repo.id)]  # noqa: E731
    tests = [t["path"] for t in aff["tests_to_run"]]
    docs = same(aff["docs_to_update"])
    deps = same(aff["dependents"])
    code_paths = [f["path"] for f in aff["files"] if oracle._is_code(f["path"])]
    who = ownership.experts(db, repo.id, code_paths[:20]) if code_paths else []
    top = [s for s in sorted(r["signals"], key=lambda s: -s["points"]) if s["points"] > 0][:3]

    lines = [MARKER, f"### RepoLens review: {LEVEL[r['level']]} ({round(r['score'])}/100)", ""]
    if top:
        lines += ["**Why**", *[f"- {s['label']}: {s['detail']}" for s in top], ""]
    else:
        lines += ["Nothing in this change stands out.", ""]
    if who:
        lines += [
            "**Who knows this code:** "
            + ", ".join(f"{w['author']} ({round(w['share'] * 100)}% of recent work in these files)" for w in who[:3]),
            "",
        ]
    if tests:
        lines += ["**Tests that cover it:** " + _code(tests, 6), ""]
    else:
        lines += (
            ["**Tests:** none of the indexed tests reference the changed code. Consider adding one.", ""]
            if code_paths
            else []
        )
    if deps:
        names = [d.get("qualname") or d.get("name") or d.get("path", "") for d in deps]
        lines += [
            f"**Used by {len(deps)} other place{'s' if len(deps) != 1 else ''}:** " + _code([n for n in names if n], 5),
            "",
        ]
    if docs:
        titles = [d.get("title") or d.get("path", "") for d in docs]
        lines += [
            "**Documentation that describes this code (may need an update):** " + _code([t for t in titles if t], 4),
            "",
        ]
    link = f" · [Open in RepoLens]({app_url.rstrip('/')}/risk)" if app_url else ""
    lines += ["<sub>Computed from your indexed repository; a guide for reviewers, not a verdict." + link + "</sub>"]
    return {
        "markdown": "\n".join(lines).rstrip() + "\n",
        "score": r["score"],
        "level": r["level"],
        "ref": f"PR #{pr_number}" if pr_number else r["ref"],
        "who": [w["author"] for w in who[:3]],
        "tests": tests[:15],
        "docs": [d.get("title") or d.get("path", "") for d in docs],
        "dependents": len(deps),
        "indexed_sha": repo.indexed_sha,
    }


def _owner_name(url: str) -> str:
    """`owner/name` from an https or ssh clone address (`https://github.com/o/n.git`, `git@github.com:o/n`), else ''."""
    u = url.strip().lower().removesuffix("/").removesuffix(".git")
    tail = u.rsplit(":", 1)[-1] if u.startswith("git@") else u.split("://", 1)[-1].split("/", 1)[-1]
    parts = [x for x in tail.split("/") if x]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def find_repo(db: Session, org_id: str, scope: Scope, ref: str) -> Repository | None:
    """A repository by id, by exact name, or by the GitHub `owner/name` it was cloned from."""
    from sqlalchemy import select

    rows = [r for r in db.scalars(select(Repository).where(Repository.org_id == org_id)) if scope.can(r.id)]
    ref = ref.strip()
    for r in rows:
        if r.id == ref or r.name == ref:
            return r
    want = ref.lower().removesuffix(".git")
    for r in rows:
        if want in (_owner_name(r.url or ""), _owner_name(r.web_url or "")):
            return r
    return None
