"""Oracle: interpretable change-risk scoring.

R = 100 * sum(w_i * s_i), weights sum to 1, each signal s_i in [0, 1]. The score ranks changes for attention; it is
NOT a probability of failure. Every assessment returns its signals, evidence, model version and known limitations.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Change, Investigation, KGEdge, KGNode, PullRequest, Repository
from . import gitops
from .access import Scope
from .embeddings import Embedder, cosine
from .graph import TEST_PATH
from .indexer import resolve_checkout

MODEL_VERSION = "oracle-heuristic-v1"
WEIGHTS = {
    "size": 0.15,
    "centrality": 0.20,
    "interface": 0.10,
    "history": 0.20,
    "tests": 0.15,
    "sensitive": 0.10,
    "similar": 0.10,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
LEVELS = [(75, "critical"), (50, "high"), (25, "medium"), (0, "low")]

LIMITATIONS = [
    "A prioritisation heuristic, not a calibrated probability of failure.",
    "Test coverage is a static proxy (test files that call the changed code), not measured line coverage.",
    "Dependents come from static analysis: dynamic dispatch, reflection and runtime wiring are not seen.",
    "Historical signals need history: young repositories have little to learn from and score lower on them.",
    "Changes without local git objects (e.g. PRs known only from GitHub) are assessed at file level, not symbol level.",
]

FIX_RE = re.compile(
    r"\b(fix(es|ed)?|bug|hotfix|regression|revert|rollback|incident|patch)\b|\b[A-Z][A-Z0-9]+-\d+\b", re.I
)
IFACE_PATH = re.compile(
    r"(^|/)(api|apis|routes?|routers?|controllers?|handlers?|endpoints?|views?|schemas?|graphql|public|sdk)(/|\.|_)"
    r"|openapi|swagger|\.proto$|\.graphql$",
    re.I,
)
SENSITIVE_HIGH = re.compile(
    r"(auth|login|oauth|jwt|token|secret|credential|passw|crypto|tls|ssl|cert|permission|rbac|acl|"
    r"security|payment|billing|invoice|migrations?/|alembic|schema\.sql)",
    re.I,
)
SENSITIVE_MED = re.compile(
    r"(^|/)(config|settings|conf)(/|\.|_)|\.env|\.ya?ml$|\.toml$|\.ini$|dockerfile|docker-compose|"
    r"\.github/workflows|jenkinsfile|(^|/)k8s/|helm|terraform|\.tf$|package\.json$|requirements.*\.txt$|"
    r"pyproject\.toml$|go\.mod$|pom\.xml$|gemfile|\.lock$",
    re.I,
)
DOC_RE = re.compile(r"\.(md|mdx|rst|txt|adoc)$", re.I)
LOCKFILES = ("package-lock.json", "yarn.lock", "poetry.lock", "pnpm-lock.yaml", "uv.lock", "Cargo.lock")


@dataclass
class FileChange:
    path: str
    additions: int = 0
    deletions: int = 0
    ranges: list[tuple[int, int]] | None = None  # new-side line ranges; None = unknown (file-level only)


@dataclass
class ChangeInput:
    repo: Repository
    files: list[FileChange]
    ref_type: str
    ref: str
    title: str = ""
    exclude_sha: str | None = None
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))


def level_for(score: float) -> str:
    return next(name for floor, name in LEVELS if score >= floor)


# ------------------------------------------------------------------------------------------------ loaders
def _checkout(repo: Repository) -> str | None:
    if repo.source == "docs":
        return None
    try:
        return resolve_checkout(repo) if repo.source == "local" else (repo.local_path or None)
    except gitops.GitError:
        return None


def from_commit(db: Session, repo: Repository, sha: str) -> ChangeInput:
    ch = db.scalar(select(Change).where(Change.repo_id == repo.id, Change.sha.startswith(sha)))
    if ch is None:
        raise ValueError("commit not found in the index; re-index the repository")
    ranges: dict[str, list[tuple[int, int]]] = {}
    co = _checkout(repo)
    if co:
        try:
            ranges = gitops.commit_hunks(co, ch.sha)
        except gitops.GitError:
            ranges = {}
    files = [
        FileChange(f["path"], f.get("additions", 0), f.get("deletions", 0), ranges.get(f["path"])) for f in ch.files
    ]
    as_of = ch.committed_at if ch.committed_at.tzinfo else ch.committed_at.replace(tzinfo=UTC)
    return ChangeInput(repo, files, "commit", ch.sha, ch.message.splitlines()[0] if ch.message else "", ch.sha, as_of)


def from_pr(db: Session, repo: Repository, number: int) -> ChangeInput:
    pr = db.scalar(select(PullRequest).where(PullRequest.repo_id == repo.id, PullRequest.number == number))
    if pr is None:
        raise ValueError("pull request not found; sync pull requests first")
    files = [FileChange(f["path"], f.get("additions", 0), f.get("deletions", 0)) for f in pr.files]
    return ChangeInput(repo, files, "pr", f"#{number}", pr.title)


def from_range(repo: Repository, base: str, head: str) -> ChangeInput:
    co = _checkout(repo)
    if not co:
        raise ValueError("this repository has no local checkout to diff")
    b, h = gitops.resolve_ref(co, base), gitops.resolve_ref(co, head)
    mb = gitops.merge_base(co, b, h)  # what a merge of `head` into `base` would change
    ranges = gitops.range_hunks(co, mb, h)
    files = [
        FileChange(f["path"], f["additions"], f["deletions"], ranges.get(f["path"]))
        for f in gitops.diff_numstat(co, mb, h)
    ]
    return ChangeInput(repo, files, "range", f"{base}...{head}", f"{base}...{head}")


def from_files(repo: Repository, files: list[dict], title: str = "") -> ChangeInput:
    return ChangeInput(
        repo,
        [FileChange(f["path"], f.get("additions", 0), f.get("deletions", 0)) for f in files],
        "files",
        f"{len(files)} files",
        title,
    )


# ------------------------------------------------------------------------------------------------ analysis
@dataclass
class _Graph:
    file_nodes: dict[str, KGNode]
    changed_symbols: list[KGNode]
    dependents: list[dict]  # {node, via, rel, evidence}
    tests: list[dict]
    docs: list[dict]


def _changed_symbols(db: Session, repo: Repository, files: list[FileChange]) -> list[KGNode]:
    paths = [f.path for f in files]
    nodes = list(
        db.scalars(
            select(KGNode).where(
                KGNode.repo_id == repo.id, KGNode.path.in_(paths or ["-"]), KGNode.type.in_(("function", "class"))
            )
        )
    )
    by_path: dict[str, list[KGNode]] = {}
    for n in nodes:
        by_path.setdefault(n.path, []).append(n)
    out: list[KGNode] = []
    for f in files:
        if not f.ranges:
            continue
        hit = [n for n in by_path.get(f.path, []) if any(n.start_line <= e and s <= n.end_line for s, e in f.ranges)]
        funcs = [n for n in hit if n.type == "function"]
        classes = [
            n for n in hit if n.type == "class" and not any(fn.qualname.startswith(n.qualname + ".") for fn in funcs)
        ]
        out += funcs + classes
    return out


def _graph_context(db: Session, ci: ChangeInput, scope: Scope | None = None) -> _Graph:
    org, repo = ci.repo.org_id, ci.repo
    paths = [f.path for f in ci.files]
    file_nodes = {
        n.path: n
        for n in db.scalars(
            select(KGNode).where(
                KGNode.repo_id == repo.id, KGNode.type.in_(("file", "doc")), KGNode.path.in_(paths or ["-"])
            )
        )
    }
    symbols = _changed_symbols(db, repo, ci.files)
    targets = {n.id: n for n in [*file_nodes.values(), *symbols]}
    dependents: list[dict] = []
    tests: list[dict] = []
    docs: list[dict] = []
    if targets:
        edges = list(
            db.scalars(
                select(KGEdge).where(
                    KGEdge.org_id == org,
                    KGEdge.dst_id.in_(list(targets)),
                    KGEdge.rel.in_(("CALLS", "IMPORTS", "TESTS", "MENTIONS", "RELATED_TO")),
                )
            )
        )
        srcs = {n.id: n for n in db.scalars(select(KGNode).where(KGNode.id.in_({e.src_id for e in edges} or {"-"})))}
        names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == org))}
        seen: set[tuple[str, str]] = set()
        for e in edges:
            src, dst = srcs.get(e.src_id), targets[e.dst_id]
            if scope is not None and (src is None or not scope.can(src.repo_id)):
                continue  # a dependent in a repository the viewer cannot see is not mentioned at all
            if (
                src is None
                or src.path in {f.path for f in ci.files}
                and src.repo_id == repo.id
                and e.rel in ("CALLS", "IMPORTS")
            ):
                continue  # internal to the change itself
            item = {
                "node_id": src.id,
                "type": src.type,
                "name": src.qualname or src.name,
                "path": src.path,
                "repo": names.get(src.repo_id, ""),
                "repo_id": src.repo_id,
                "cross_repo": src.repo_id != repo.id,
                "rel": e.rel,
                "target": dst.qualname or dst.name,
                "provenance": e.provenance,
                "evidence": e.evidence or f"{src.name} {e.rel.lower()} {dst.name}",
            }
            key = (src.id, e.rel)
            if key in seen:
                continue
            seen.add(key)
            if e.rel == "TESTS" or (e.rel == "CALLS" and TEST_PATH.search(src.path)):
                tests.append(item)
            elif e.rel in ("MENTIONS", "RELATED_TO") and src.type == "section":
                docs.append(item)
            elif e.rel in ("CALLS", "IMPORTS"):
                dependents.append(item)
    return _Graph(file_nodes, symbols, dependents, tests, docs)


def _is_code(path: str) -> bool:
    return (
        not DOC_RE.search(path)
        and not TEST_PATH.search(path)
        and bool(re.search(r"\.(py|js|jsx|ts|tsx|go|java)$", path))
    )


# ------------------------------------------------------------------------------------------------ signals
def _sig_size(ci: ChangeInput) -> tuple[float, str, list]:
    real = [f for f in ci.files if not f.path.endswith(LOCKFILES)]
    lines = sum(f.additions + f.deletions for f in real)
    s = 0.6 * min(1.0, math.log1p(lines) / math.log1p(1000)) + 0.4 * min(1.0, len(real) / 20)
    return s, f"{lines} lines changed across {len(real)} files", []


def _sig_centrality(g: _Graph) -> tuple[float, str, list]:
    d = {x["node_id"] for x in g.dependents}
    s = min(1.0, math.log1p(len(d)) / math.log1p(20))
    cross = sum(1 for x in g.dependents if x["cross_repo"])
    detail = (
        f"{len(d)} direct dependents" + (f" ({cross} in other repositories)" if cross else "")
        if d
        else "no known dependents"
    )
    return s, detail, g.dependents[:10]


def _sig_interface(ci: ChangeInput, g: _Graph) -> tuple[float, str, list]:
    hits = [
        f.path
        for f in ci.files
        if IFACE_PATH.search(f.path) and not TEST_PATH.search(f.path) and not DOC_RE.search(f.path)
    ]
    public = [n for n in g.changed_symbols if not n.name.startswith("_")]
    frac = len(public) / len(g.changed_symbols) if g.changed_symbols else 0.0
    s = min(1.0, (0.6 if hits else 0.0) + 0.4 * frac * (1.0 if g.dependents else 0.5))
    parts = []
    if hits:
        parts.append(f"touches interface paths ({', '.join(hits[:3])})")
    if g.changed_symbols:
        parts.append(f"{len(public)}/{len(g.changed_symbols)} changed symbols are public")
    return s, "; ".join(parts) or "no interface paths or public symbols detected", [{"path": p} for p in hits[:5]]


def _sig_sensitive(ci: ChangeInput) -> tuple[float, str, list]:
    best, cats, ev = 0.0, set(), []
    for f in ci.files:
        if DOC_RE.search(f.path) or TEST_PATH.search(f.path):
            continue
        if SENSITIVE_HIGH.search(f.path):
            best, cat = max(best, 1.0), "auth/crypto/payments/migrations"
        elif SENSITIVE_MED.search(f.path):
            best, cat = max(best, 0.6), "configuration/CI/dependencies"
        else:
            continue
        cats.add(cat)
        ev.append({"path": f.path, "category": cat})
    s = min(1.0, best + 0.1 * max(len(cats) - 1, 0))
    return s, ("sensitive areas: " + ", ".join(sorted(cats))) if cats else "no sensitive paths", ev[:6]


def _sig_tests(ci: ChangeInput, g: _Graph) -> tuple[float, str, list]:
    code = [f.path for f in ci.files if _is_code(f.path)]
    if not code:
        return 0.0, "no production code changed", []
    covered_files = {t["target"] for t in g.tests}
    test_edges_files = set()
    for p in code:
        n = g.file_nodes.get(p)
        if n is not None and any(t["target"] in (n.name, n.qualname) for t in g.tests):
            test_edges_files.add(p)
    # symbol-level: tests that call changed symbols also count for the file that holds them
    for sym in g.changed_symbols:
        if sym.qualname in covered_files or sym.name in covered_files:
            test_edges_files.add(sym.path)
    uncovered = [p for p in code if p not in test_edges_files]
    frac = len(uncovered) / len(code)
    tests_touched = any(TEST_PATH.search(f.path) for f in ci.files)
    s = frac * (0.6 if tests_touched else 1.0)
    detail = f"{len(code) - len(uncovered)}/{len(code)} changed code files have tests that reference them"
    if tests_touched:
        detail += "; the change also updates tests"
    return s, detail, [{"path": p, "covered": p not in uncovered} for p in code[:8]]


def _fix_commits(db: Session, ci: ChangeInput) -> list[Change]:
    paths = {f.path for f in ci.files}
    since = ci.as_of - timedelta(days=365)
    rows = db.scalars(
        select(Change)
        .where(Change.repo_id == ci.repo.id, Change.committed_at < ci.as_of, Change.committed_at >= since)
        .order_by(Change.committed_at.desc())
        .limit(1500)
    )
    return [
        c
        for c in rows
        if c.sha != ci.exclude_sha and FIX_RE.search(c.message or "") and paths & {f["path"] for f in c.files}
    ]


def _sig_history(db: Session, ci: ChangeInput) -> tuple[float, str, list]:
    fixes = _fix_commits(db, ci)
    weight = 0.0
    ev: list[dict] = []
    for c in fixes:
        dt = c.committed_at if c.committed_at.tzinfo else c.committed_at.replace(tzinfo=UTC)
        w = 1.0 / (1.0 + max((ci.as_of - dt).days, 0) / 180.0)
        weight += w
        ev.append(
            {
                "type": "fix_commit",
                "sha": c.sha[:10],
                "message": (c.message or "").splitlines()[0][:100],
                "touches": sorted({f["path"] for f in c.files} & {f.path for f in ci.files})[:3],
            }
        )
    paths = {f.path for f in ci.files}
    incidents = 0
    for inv in db.scalars(
        select(Investigation).where(
            Investigation.org_id == ci.repo.org_id, Investigation.root_cause_status == "confirmed"
        )
    ):
        top = inv.hypotheses[0] if inv.hypotheses else None
        files = {e.get("file") for e in (top.evidence if top else []) if e.get("type") == "code"}
        if files & paths:
            incidents += 1
            ev.append({"type": "confirmed_incident", "investigation": inv.id, "title": inv.title[:100]})
    s = min(1.0, (weight + 1.5 * incidents) / 4.0)
    return s, f"{len(fixes)} earlier fix/revert commits and {incidents} confirmed incidents touched these files", ev[:8]


def _sig_similar(db: Session, ci: ChangeInput) -> tuple[float, str, list]:
    text = ci.title + "\n" + " ".join(f.path for f in ci.files)
    emb = Embedder()
    qv = emb.embed([text])[0]
    past = list(
        db.scalars(
            select(Change)
            .where(Change.repo_id == ci.repo.id, Change.committed_at < ci.as_of)
            .order_by(Change.committed_at.desc())
            .limit(600)
        )
    )
    cands = [(cosine(qv, c.embedding), c) for c in past if c.embedding and c.sha != ci.exclude_sha]
    cands = sorted([x for x in cands if x[0] >= 0.25], key=lambda x: -x[0])[:8]
    if len(cands) < 3:
        return 0.0, f"insufficient history ({len(cands)} similar past changes; need 3)", []
    fix_pool = [c for c in past if FIX_RE.search(c.message or "")]
    bad, ev = 0, []
    for sim, c in cands:
        files = {f["path"] for f in c.files}
        dt = c.committed_at if c.committed_at.tzinfo else c.committed_at.replace(tzinfo=UTC)
        followed = any(
            fx.sha != c.sha
            and dt
            < (fx.committed_at if fx.committed_at.tzinfo else fx.committed_at.replace(tzinfo=UTC))
            <= dt + timedelta(days=21)
            and files & {f["path"] for f in fx.files}
            for fx in fix_pool
        )
        bad += followed
        ev.append(
            {
                "sha": c.sha[:10],
                "message": (c.message or "").splitlines()[0][:80],
                "similarity": round(sim, 2),
                "followed_by_fix": followed,
            }
        )
    return bad / len(cands), f"{bad} of {len(cands)} similar past changes were followed by a fix within 21 days", ev


# ------------------------------------------------------------------------------------------------ assess
def assess(db: Session, ci: ChangeInput, scope: Scope | None = None) -> dict:
    g = _graph_context(db, ci, scope)
    computed = {
        "size": _sig_size(ci),
        "centrality": _sig_centrality(g),
        "interface": _sig_interface(ci, g),
        "history": _sig_history(db, ci),
        "tests": _sig_tests(ci, g),
        "sensitive": _sig_sensitive(ci),
        "similar": _sig_similar(db, ci),
    }
    labels = {
        "size": "Change size",
        "centrality": "Dependency centrality",
        "interface": "Public interfaces",
        "history": "Historical defects",
        "tests": "Test coverage gap",
        "sensitive": "Sensitive areas",
        "similar": "Similar past changes",
    }
    signals = []
    score = 0.0
    for key, (value, detail, evidence) in computed.items():
        contribution = 100.0 * WEIGHTS[key] * value
        score += contribution
        signals.append(
            {
                "key": key,
                "label": labels[key],
                "weight": WEIGHTS[key],
                "value": round(value, 3),
                "points": round(contribution, 1),
                "detail": detail,
                "evidence": evidence,
            }
        )
    score = round(score, 1)
    changed = [{"path": f.path, "additions": f.additions, "deletions": f.deletions} for f in ci.files]
    uniq_dep = {d["node_id"]: d for d in g.dependents}
    affected = {
        "files": changed[:60],
        "symbols": [{"name": n.qualname, "path": n.path, "type": n.type} for n in g.changed_symbols[:40]],
        "dependents": list(uniq_dep.values())[:30],
        "tests_to_run": list({t["path"]: t for t in g.tests}.values())[:15],
        "docs_to_update": list({d["node_id"]: d for d in g.docs}.values())[:10],
        "symbol_level": any(f.ranges for f in ci.files),
    }
    return {
        "score": score,
        "level": level_for(score),
        "model_version": MODEL_VERSION,
        "signals": signals,
        "affected": affected,
        "limitations": LIMITATIONS,
        "ref_type": ci.ref_type,
        "ref": ci.ref,
        "title": ci.title,
    }


def rank_recent_commits(db: Session, repo: Repository, limit: int = 20, scope: Scope | None = None) -> list[dict]:
    """Score the latest commits (no persistence) so reviewers can see what deserves attention first."""
    rows = db.scalars(select(Change).where(Change.repo_id == repo.id).order_by(Change.committed_at.desc()).limit(limit))
    out = []
    for c in rows:
        try:
            r = assess(db, from_commit(db, repo, c.sha), scope)
        except ValueError:
            continue
        top = sorted(r["signals"], key=lambda s: -s["points"])[:2]
        out.append(
            {
                "sha": c.sha,
                "title": r["title"],
                "author": c.author,
                "committed_at": c.committed_at,
                "score": r["score"],
                "level": r["level"],
                "top_signals": [s["label"] for s in top if s["points"] > 0],
            }
        )
    return sorted(out, key=lambda x: -x["score"])


__all__ = ["assess", "from_commit", "from_pr", "from_range", "from_files", "rank_recent_commits", "WEIGHTS"]
