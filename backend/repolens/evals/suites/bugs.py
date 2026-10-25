"""Real-bug evaluation mined from a repository's own history; nothing is downloaded and nothing leaves the machine.

A "fix" commit is a bug that someone found and repaired. We take its message as the bug report, index the repository exactly as it
was *before* the fix (the later history is not reachable), and ask whether RepoLens points at the files the fix really changed.

Limits worth stating next to any number from here: the report is the author's commit message, which is tidier than a real ticket;
file paths named in it are masked; a repository's own history is a small sample, so read the confidence interval; and the model
is never called (hash embedder, rule-based reasoning), so this measures retrieval and ranking, not a language model."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..metrics import mean, mrr, recall_at_k, wilson

FIXY = re.compile(r"\b(fix(es|ed)?|bugfix|hotfix|resolve[sd]?|repair(ed)?|correct(ed)?)\b", re.I)
CODE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".java",
    ".rb",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".php",
    ".sh",
}
NOT_CODE = re.compile(
    r"(^|/)(tests?|__tests__|e2e|docs?|changelog|examples?|node_modules|alembic|migrations)(/|$)|\.test\.|\.spec\.|_test\.",
    re.I,
)


def _git(path: str, *args: str, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout


def mine(path: str, limit: int = 20, max_files: int = 4) -> list[dict]:
    """Fix commits with a usable answer: 1..max_files modified (not added) source files, newest first."""
    raw = _git(path, "log", "--no-merges", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", "-n", "2000")
    cases = []
    for rec in raw.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 4 or not parts[1].strip():
            continue
        sha, parent, subject, body = parts[0], parts[1].split()[0], parts[2], parts[3]
        if not FIXY.search(subject):
            continue
        changed = [
            ln.split("\t") for ln in _git(path, "show", "--name-status", "--format=", sha).splitlines() if "\t" in ln
        ]
        truth = [f[1] for f in changed if f[0] == "M" and Path(f[1]).suffix in CODE_EXT and not NOT_CODE.search(f[1])]
        if not truth or len(truth) > max_files:
            continue
        query = f"{subject}\n\n{body[:600]}".strip()
        for f in truth:  # naming the file in the report would make the question trivial
            query = query.replace(f, "<file>").replace(Path(f).name, "<file>")
        cases.append({"sha": sha, "parent": parent, "subject": subject, "query": query, "truth": truth})
        if len(cases) >= limit:
            break
    return cases


def _snapshot(src: str, parent: str, dest: str) -> None:
    """A clone whose only reachable history ends at `parent`: the fix and everything after it are invisible to the index."""
    _git(".", "clone", "-q", "--shared", "--no-checkout", src, dest)
    _git(dest, "update-ref", "refs/heads/eval", parent)
    _git(dest, "symbolic-ref", "HEAD", "refs/heads/eval")


def _files_in_order(hits) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h.chunk.file_path not in seen:
            seen.append(h.chunk.file_path)
    return seen


def _investigation_files(inv) -> list[str]:
    seen: list[str] = []
    for h in inv.hypotheses:
        for e in h.evidence:
            if e.get("type") == "code" and e.get("file") not in seen:
                seen.append(e["file"])
    return seen


def run(repo_path: str, limit: int = 20, investigate: bool = True) -> dict:
    from ... import db
    from ...config import get_settings
    from ...models import Organization, Repository, User
    from ...security import hash_password
    from ...services import indexer, retrieval
    from ...services.embeddings import Embedder
    from ...services.investigations import create_investigation, run_investigation
    from .embedders import HASH

    HASH.apply()
    os.environ["REPOLENS_LLM_PROVIDER"] = "heuristic"  # never a hosted model, whatever the environment says
    get_settings.cache_clear()
    repo_path = str(Path(repo_path).expanduser().resolve())
    cases = mine(repo_path, limit)
    work = Path(tempfile.mkdtemp(prefix="repolens-bugs-eval-"))
    db.init_engine(f"sqlite:///{work / 'eval.db'}")
    db.create_all()
    s = db.session_factory()()
    org = Organization(name="eval")
    s.add(org)
    s.flush()
    user = User(
        org_id=org.id, email="eval@example.test", password_hash=hash_password("unused-eval-password-1"), role="admin"
    )
    s.add(user)
    s.commit()
    embedder = Embedder()
    rows, r_ranks, i_ranks, rand3 = [], [], [], []
    for n, c in enumerate(cases):
        dest = str(work / f"snap{n}")
        _snapshot(repo_path, c["parent"], dest)
        repo = Repository(org_id=org.id, name=f"snap{n}", source="local", url=dest, status="pending")
        s.add(repo)
        s.commit()
        indexer.index_repository(s, repo.id, max_commits=100)
        s.refresh(repo)
        if repo.status != "ready":
            continue
        order = _files_in_order(
            retrieval.search_chunks(s, org.id, c["query"], [repo.id], k=40, embedder=embedder, rerank="lexical")
        )
        truth = [t for t in c["truth"] if t in set(_git(dest, "ls-tree", "-r", "--name-only", "HEAD").splitlines())]
        if not truth:
            continue
        rank = next((i + 1 for i, f in enumerate(order) if f in truth), None)
        irank = None
        if investigate:
            inv = create_investigation(s, org.id, user.id, c["query"], [repo.id])
            run_investigation(inv.id)
            s.expire_all()
            from ...models import Investigation

            got = s.get(Investigation, inv.id)
            irank = next((i + 1 for i, f in enumerate(_investigation_files(got)) if f in truth), None) if got else None
        r_ranks.append(rank)
        i_ranks.append(irank)
        rand3.append(min(1.0, 3 * len(truth) / max(1, repo.file_count)))
        rows.append(
            {
                "commit": c["sha"][:8],
                "report": c["subject"][:90],
                "files": truth,
                "retrieval_rank": rank,
                "investigation_rank": irank,
            }
        )
    n = len(r_ranks)

    def block(ranks: list[int | None], prefix: str) -> dict:
        hit3 = sum(1 for r in ranks if r is not None and r <= 3)
        lo, hi = wilson(hit3, n)
        return {
            f"{prefix}top1": round(recall_at_k(ranks, 1), 3),
            f"{prefix}top3": round(recall_at_k(ranks, 3), 3),
            f"{prefix}top5": round(recall_at_k(ranks, 5), 3),
            f"{prefix}top10": round(recall_at_k(ranks, 10), 3),
            f"{prefix}mrr": round(mrr(ranks), 3),
            f"{prefix}top3_ci95": [round(lo, 3), round(hi, 3)],
        }

    out = {
        "cases": n,
        "random_top3": round(mean(rand3), 3) if rand3 else 0.0,
        **(block(r_ranks, "retrieval_") if n else {}),
    }
    if investigate and n:
        out.update(block(i_ranks, "investigation_"))
    out["rows"] = rows
    return out
