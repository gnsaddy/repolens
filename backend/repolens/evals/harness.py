"""Ablation harness: does each architectural component improve accuracy or cost?

Configurations: keyword search -> semantic RAG -> hybrid RAG -> + git history -> + dependency graph ->
full investigation with local memory. Everything runs offline against synthetic repositories with known regressions.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.workflow import Features
from ..db import create_all, init_engine, session_factory
from ..models import (
    Change,
    CodeChunk,
    Investigation,
    LLMCallEvent,
    MemoryEntry,
    Organization,
    Repository,
)
from ..services import gitops
from ..services import risk as oracle
from ..services.indexer import index_repository
from ..services.investigations import create_investigation, run_investigation
from ..services.retrieval import search_chunks
from . import metrics as m
from .generator import Case, generate

DEPTH = 30


@dataclass
class Config:
    key: str
    name: str
    kind: str  # retrieval | workflow
    features: Features
    memory_pass: bool = False


CONFIGS = [
    Config(
        "keyword",
        "1. Keyword search",
        "retrieval",
        Features(search_mode="lexical", stack_frames=False, history=False, graph=False, memory=False),
    ),
    Config(
        "semantic",
        "2. Semantic RAG (vector only)",
        "retrieval",
        Features(search_mode="vector", stack_frames=False, history=False, graph=False, memory=False),
    ),
    Config(
        "hybrid",
        "3. Hybrid RAG (vector + keyword + stack frames)",
        "retrieval",
        Features(search_mode="hybrid", history=False, graph=False, memory=False),
    ),
    Config(
        "history_noblame",
        "4. Hybrid RAG + Git history (diffs, changed lines)",
        "workflow",
        Features(history=True, blame=False, graph=False, memory=False),
    ),
    Config(
        "history",
        "5. Hybrid RAG + Git history + blame",
        "workflow",
        Features(history=True, blame=True, graph=False, memory=False),
    ),
    Config("graph", "6. Hybrid RAG + dependency graph", "workflow", Features(history=False, graph=True, memory=False)),
    Config(
        "full",
        "7. Full investigation (history + blame + graph) + local memory",
        "workflow",
        Features(history=True, graph=True, memory=True),
        memory_pass=True,
    ),
    Config(
        "hybrid_rerank",
        "8. Hybrid RAG + reranker (offline lexical cross-scoring)",
        "retrieval",
        Features(search_mode="hybrid", rerank="lexical", history=False, graph=False, memory=False),
    ),
    Config(
        "full_rerank",
        "9. Full investigation + reranker",
        "workflow",
        Features(history=True, graph=True, memory=True, rerank="lexical"),
        memory_pass=True,
    ),
]


def _pin_ablation_defaults() -> None:
    """Ablation configs are explicit: unless a config asks for a reranker, it runs without one, whatever the server default."""
    for c in CONFIGS:
        if c.features.rerank is None:
            c.features.rerank = "none"


_pin_ablation_defaults()


@dataclass
class Run:
    case_id: str
    config: str
    scope: str  # case | org
    ranked: list[tuple[str, str, str, str | None]]  # (repo_id, file, symbol, commit_sha)
    first_rank_symbol: int | None
    first_rank_file: int | None
    commit_top1: bool | None
    supported_frac: float | None
    citations_ok: bool | None
    citation_hits_gold: bool | None
    duration_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    cache_hits: int = 0
    tokens_avoided: int = 0
    failed_calls: int = 0
    llm_calls: int = 0
    evidence_kinds: list[str] = field(default_factory=list)
    memory_hits: int = 0


# ------------------------------------------------------------------------------------------------ helpers
def _match(row: tuple[str, str, str, str | None], case: Case, repo_id: str, file_only: bool = False) -> bool:
    rid, f, sym, _ = row
    if rid != repo_id or f != case.gold_file:
        return False
    gold_sym = case.gold_symbol.split(".")[-1] if case.gold_symbol else ""
    return file_only or not gold_sym or sym == gold_sym


def _dedupe(rows: list[tuple[str, str, str, str | None]]) -> list[tuple[str, str, str, str | None]]:
    seen, out = set(), []
    for r in rows:
        k = (r[0], r[1], r[2])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _retrieval_rows(db: Session, org_id: str, query: str, repo_ids: list[str], f: Features) -> tuple[list, list]:
    hits = search_chunks(db, org_id, query, repo_ids or None, k=DEPTH, mode=f.search_mode, rerank=f.rerank)
    if f.stack_frames:  # stack-trace frames are resolved to chunks exactly as the investigator does
        from ..services.retrieval import extract_stack_refs

        extra = []
        for ref in sorted(extract_stack_refs(query), key=lambda r: not r.get("innermost")):
            q = select(CodeChunk).where(
                CodeChunk.org_id == org_id, CodeChunk.start_line <= ref["line"], CodeChunk.end_line >= ref["line"]
            )
            if repo_ids:
                q = q.where(CodeChunk.repo_id.in_(repo_ids))
            base = ref["file"].replace("\\", "/")
            extra += [
                c for c in db.scalars(q) if c.file_path == base or c.file_path.endswith("/" + base.split("/")[-1])
            ]
        rows = [(c.repo_id, c.file_path, c.symbol_name, None) for c in extra] + [
            (h.chunk.repo_id, h.chunk.file_path, h.chunk.symbol_name, None) for h in hits
        ]
        chunks = extra + [h.chunk for h in hits]
    else:
        rows = [(h.chunk.repo_id, h.chunk.file_path, h.chunk.symbol_name, None) for h in hits]
        chunks = [h.chunk for h in hits]
    return rows, chunks


def _citation_checks(repo: Repository, chunk_like: list[dict], case: Case) -> tuple[bool | None, bool | None]:
    """(every cited snippet really is the text at that location in HEAD, a citation covers the gold lines)."""
    if not chunk_like:
        return None, None
    ok, hits_gold = True, False
    for c in chunk_like:
        try:
            raw = (
                gitops.read_file(repo.url, repo.indexed_sha or "HEAD", c["file"]).decode(errors="replace").splitlines()
            )
        except gitops.GitError:
            ok = False
            continue
        actual = "\n".join(raw[c["start_line"] - 1 : c["end_line"]]).strip()
        first = (c.get("snippet") or c.get("content") or "").strip().splitlines()
        if first and first[0].strip() not in actual:
            ok = False
        if (
            c["file"] == case.gold_file
            and c["start_line"] <= case.gold_lines[1]
            and c["end_line"] >= case.gold_lines[0]
        ):
            hits_gold = True
    return ok, hits_gold


def _usage(db: Session, workflow_id: str) -> dict:
    evs = list(
        db.scalars(select(LLMCallEvent).where(LLMCallEvent.workflow_id == workflow_id, LLMCallEvent.kind == "chat"))
    )
    return {
        "tokens_in": sum(e.input_tokens for e in evs),
        "tokens_out": sum(e.output_tokens for e in evs),
        "cost": sum(e.estimated_cost for e in evs),
        "cache_hits": sum(1 for e in evs if e.cache_hit),
        "tokens_avoided": sum(e.tokens_avoided for e in evs),
        "failed_calls": sum(1 for e in evs if not e.success),
        "llm_calls": len(evs),
    }


# ------------------------------------------------------------------------------------------------ runners
def run_retrieval(
    db: Session, org_id: str, cfg: Config, case: Case, repo: Repository, repo_ids: list[str], scope: str
) -> Run:
    t0 = time.time()
    rows, chunks = _retrieval_rows(db, org_id, case.bug_report, repo_ids, cfg.features)
    ms = int((time.time() - t0) * 1000)
    ranked = _dedupe(rows)[:DEPTH]
    top = chunks[0] if chunks else None
    cites = (
        [{"file": top.file_path, "start_line": top.start_line, "end_line": top.end_line, "content": top.content}]
        if top
        else []
    )
    cite_repo = (db.get(Repository, top.repo_id) if top else None) or repo
    ok, gold = _citation_checks(cite_repo, cites, case)
    return Run(
        case.id,
        cfg.key,
        scope,
        ranked,
        m.first_rank([_match(r, case, repo.id) for r in ranked]),
        m.first_rank([_match(r, case, repo.id, True) for r in ranked]),
        None,
        None,
        ok,
        gold,
        ms,
    )


def run_workflow_case(
    db: Session, org_id: str, user_id: str, cfg: Config, case: Case, repo: Repository, repo_ids: list[str], scope: str
) -> Run:
    t0 = time.time()
    inv = create_investigation(db, org_id, user_id, case.bug_report, repo_ids, title=case.id)
    run_investigation(inv.id, cfg.features)
    ms = int((time.time() - t0) * 1000)
    db.expire_all()
    loaded = db.get(Investigation, inv.id)
    assert loaded is not None
    inv = loaded
    assert inv.status == "completed", inv.error if inv else "missing"
    rows: list[tuple[str, str, str, str | None]] = []
    kinds: set[str] = set()
    cites: list[dict] = []
    for i, h in enumerate(inv.hypotheses):
        code = next((e for e in h.evidence if e["type"] == "code"), None)
        commit = next((e for e in h.evidence if e["type"] == "commit"), None)
        kinds |= {e["type"] for e in h.evidence}
        if code:
            rows.append((code["repo_id"], code["file"], code["symbol"], commit["sha"] if commit else None))
            if i == 0:
                cites = [e for e in h.evidence if e["type"] == "code"]
    fill, _ = _retrieval_rows(db, org_id, case.bug_report, repo_ids, Features(search_mode="hybrid"))
    ranked = _dedupe(rows + fill)[:DEPTH]
    top_commit = rows[0][3] if rows else None
    supported = [h.status == "supported" for h in inv.hypotheses]
    ok, gold = _citation_checks(repo, cites, case)
    u = _usage(db, inv.id)
    return Run(
        case.id,
        cfg.key,
        scope,
        ranked,
        m.first_rank([_match(r, case, repo.id) for r in ranked]),
        m.first_rank([_match(r, case, repo.id, True) for r in ranked]),
        (top_commit == case.gold_commit) if cfg.features.history else None,
        (sum(supported) / len(supported)) if supported else None,
        ok,
        gold,
        ms,
        **u,
        evidence_kinds=sorted(kinds),
        memory_hits=len(inv.evidence.get("memory", [])),
    )


def settle_memory(db: Session, run: Run, case: Case, repo_id: str) -> None:
    """Simulated reviewer for the memory configuration: confirm correct diagnoses, reject wrong ones."""
    correct = run.first_rank_symbol == 1
    inv = db.scalar(
        select(Investigation).where(Investigation.title == case.id).order_by(Investigation.created_at.desc())
    )
    if inv is None:
        return
    for mem in db.scalars(select(MemoryEntry).where(MemoryEntry.source_id == inv.id)):
        if correct:
            mem.verified = True
        else:
            db.delete(mem)
    inv.root_cause_status = "confirmed" if correct else "rejected"
    db.commit()


# ------------------------------------------------------------------------------------------------ risk
def evaluate_risk(db: Session, cases: list[Case], repo_ids: dict[str, str]) -> dict:
    """Does the regression-introducing commit outrank the other commits? Reported with chance baselines, and also
    without the initial import (a huge first commit is not a 'change' anyone would risk-score)."""
    acc: dict[str, dict] = {k: {"pos": [], "neg": [], "ranks": [], "of": [], "pct": []} for k in ("all", "ex")}
    size_pos: list[float] = []
    size_neg: list[float] = []
    top_is_initial = 0
    per_case = []
    for case in cases:
        repo = db.get(Repository, repo_ids[case.id])
        assert repo is not None
        scored = []
        for ch in db.scalars(select(Change).where(Change.repo_id == repo.id)):
            r = oracle.assess(db, oracle.from_commit(db, repo, ch.sha))
            size = next(s for s in r["signals"] if s["key"] == "size")["value"]
            scored.append((ch.sha, r["score"], size, r["level"], not ch.parent_sha))
        gold = next(x for x in scored if x[0] == case.gold_commit)
        top_is_initial += max(scored, key=lambda x: x[1])[4]
        size_pos.append(gold[2])
        size_neg += [x[2] for x in scored if x[0] != case.gold_commit and not x[4]]
        for key, pool in (("all", scored), ("ex", [x for x in scored if not x[4]])):
            others = [x for x in pool if x[0] != case.gold_commit]
            a = acc[key]
            a["pos"].append(gold[1])
            a["neg"] += [x[1] for x in others]
            a["ranks"].append(1 + sum(1 for x in others if x[1] > gold[1]))
            a["of"].append(len(pool))
            a["pct"].append(m.percentile_rank(gold[1], [x[1] for x in others]))
        per_case.append(
            {
                "case": case.id,
                "gold_score": gold[1],
                "gold_level": gold[3],
                "rank": acc["all"]["ranks"][-1],
                "of": len(scored),
                "max_other": max((x[1] for x in scored if x[0] != case.gold_commit), default=0.0),
            }
        )

    def summary(a: dict) -> dict:
        n = len(a["ranks"])
        return {
            "auc": m.auc(a["pos"], a["neg"]),
            "top1": sum(1 for r in a["ranks"] if r == 1) / n,
            "top3": sum(1 for r in a["ranks"] if r <= 3) / n,
            "mean_percentile": m.mean(a["pct"]),
            "mean_gold_score": m.mean(a["pos"]),
            "mean_other_score": m.mean(a["neg"]),
            "chance_top1": m.mean([1 / k for k in a["of"]]),
            "chance_top3": m.mean([min(3, k) / k for k in a["of"]]),
            "mean_commits": m.mean(a["of"]),
        }

    return {
        "cases": len(cases),
        "all": summary(acc["all"]),
        "ex_initial": summary(acc["ex"]),
        "auc_size_only": m.auc(size_pos, size_neg),
        "top_scored_is_initial_import": top_is_initial / len(cases),
        "per_case": per_case,
    }


# ------------------------------------------------------------------------------------------------ orchestration
def run_benchmark(
    out: Path,
    n_cases: int = 20,
    seed: int = 7,
    org_wide: bool = True,
    risk: bool = True,
    configs: list[Config] | None = None,
    progress=print,
) -> dict:
    configs = configs or CONFIGS
    t_all = time.time()
    cases = generate(out / "dataset", n_cases, seed)
    init_engine("sqlite://")
    create_all()
    db = session_factory()()
    org = Organization(name="eval")
    db.add(org)
    db.commit()
    user_id = "eval-runner"
    repo_ids: dict[str, str] = {}
    index_stats = []
    progress(f"indexing {len(cases)} repositories…")
    for c in cases:
        repo = Repository(org_id=org.id, name=c.id, source="local", url=str((out / "dataset" / c.repo_dir).resolve()))
        db.add(repo)
        db.commit()
        t0 = time.time()
        run = index_repository(db, repo.id)
        db.refresh(repo)
        assert repo.status == "ready", run.error
        repo_ids[c.id] = repo.id
        index_stats.append(
            {
                "case": c.id,
                "ms": int((time.time() - t0) * 1000),
                "files": run.files_indexed,
                "chunks": run.chunks_written,
                "commits": run.commits_indexed,
            }
        )

    incremental = _incremental_indexing(db, cases[:5], repo_ids, out / "dataset")
    runs: list[Run] = []
    scopes = ["case"] + (["org"] if org_wide else [])
    for scope in scopes:
        for cfg in configs:
            progress(f"  {scope:4s} {cfg.name}")
            if cfg.memory_pass:
                for mem in db.scalars(select(MemoryEntry)):
                    db.delete(mem)
                db.commit()
            for case in cases:
                repo = db.get(Repository, repo_ids[case.id])
                assert repo is not None
                scope_ids = [repo.id] if scope == "case" else []
                if cfg.kind == "retrieval":
                    r = run_retrieval(db, org.id, cfg, case, repo, scope_ids, scope)
                else:
                    r = run_workflow_case(db, org.id, user_id, cfg, case, repo, scope_ids, scope)
                    if cfg.memory_pass:
                        settle_memory(db, r, case, repo.id)
                runs.append(r)
    risk_result = evaluate_risk(db, cases, repo_ids) if risk else None
    db.close()
    return {
        "cases": cases,
        "runs": runs,
        "index": index_stats,
        "incremental": incremental,
        "risk": risk_result,
        "configs": configs,
        "seed": seed,
        "elapsed_s": round(time.time() - t_all, 1),
        "scopes": scopes,
    }


def _incremental_indexing(db: Session, cases: list[Case], repo_ids: dict[str, str], root: Path) -> dict:
    """Cost of re-indexing after one new commit that touches one file."""
    import subprocess

    rows = []
    for c in cases:
        repo_path = root / c.repo_dir
        f = repo_path / c.gold_file
        f.write_text(f.read_text() + "\n\ndef _eval_extra_helper():\n    return 1\n")
        subprocess.run(["git", "-C", str(repo_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "-c",
                "user.name=e",
                "-c",
                "user.email=e@e.io",
                "commit",
                "-qm",
                "eval: extra",
            ],
            check=True,
            capture_output=True,
        )
        t0 = time.time()
        run = index_repository(db, repo_ids[c.id])
        rows.append(
            {
                "ms": int((time.time() - t0) * 1000),
                "written": run.chunks_written,
                "reused": run.chunks_reused,
                "commits": run.commits_indexed,
            }
        )
        # restore the benchmark repo to its generated state (drop the extra commit) and re-index for later runs
        subprocess.run(
            ["git", "-C", str(repo_path), "reset", "-q", "--hard", "HEAD~1"], check=True, capture_output=True
        )
        # forget the temporary commit so it cannot leak into history retrieval or risk scoring
        db.query(Change).filter(Change.repo_id == repo_ids[c.id], Change.message == "eval: extra").delete()
        db.commit()
        index_repository(db, repo_ids[c.id])
    return {
        "samples": len(rows),
        "mean_ms": m.mean([r["ms"] for r in rows]),
        "mean_written": m.mean([r["written"] for r in rows]),
        "mean_reused": m.mean([r["reused"] for r in rows]),
    }
