"""Run the real-bug evaluation on one of the organisation's own repositories, in a separate process.

The evaluation builds throwaway databases and clones, so it never shares a process or database with the server. Nothing
is downloaded and no language model is called. Each run is stored so the numbers can be compared later."""

import json
import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_factory
from ..models import EvalRun, Repository

log = logging.getLogger("repolens.measure")

MAX_CASES = 15
TIMEOUT_S = 20 * 60
STALE = timedelta(minutes=30)

_threads: dict[str, threading.Thread] = {}


def checkout_path(repo: Repository) -> str | None:
    """Where this repository's history lives on disk, if it has been fetched."""
    if repo.source == "local":
        return str(Path(repo.url).expanduser().resolve())
    return repo.local_path if repo.source == "git" and repo.local_path and Path(repo.local_path).is_dir() else None


def running(db: Session, org_id: str) -> EvalRun | None:
    cutoff = datetime.now(UTC) - STALE
    for r in db.scalars(select(EvalRun).where(EvalRun.org_id == org_id, EvalRun.status == "running")):
        started = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=UTC)
        if started < cutoff:  # the server restarted mid-run
            r.status, r.error, r.finished_at = "failed", "The run was interrupted", datetime.now(UTC)
            continue
        return r
    db.commit()
    return None


def summarise(res: dict) -> dict:
    """What a person needs from the raw numbers, with the caveats that go with them."""
    n = res.get("cases", 0)
    out = {
        "cases": n,
        "random_top3": res.get("random_top3", 0),
        "retrieval": {k: res.get(f"retrieval_{k}") for k in ("top1", "top3", "top5", "mrr")},
        "investigation": {k: res.get(f"investigation_{k}") for k in ("top1", "top3", "top5", "mrr")},
        "top3_ci95": res.get("investigation_top3_ci95") or res.get("retrieval_top3_ci95"),
        "rows": res.get("rows", [])[:MAX_CASES],
    }
    out["caveats"] = [
        "Each question is the author's own commit message for a bug fix, which is tidier than a real ticket.",
        "File names in the message are hidden. The index is rebuilt as it was just before the fix.",
        f"{n} case(s) is a small sample: read the range, not just the headline.",
        "No language model is used, so this measures finding the right files, not how well an answer reads.",
    ]
    return out


def _execute(run_id: str, path: str, cases: int) -> None:
    db = session_factory()()
    try:
        run = db.get(EvalRun, run_id)
        assert run is not None
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-m", "repolens.evals.suites", "--suite", "bugs", "--repo", path]
                + ["--cases", str(cases)],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr.strip().splitlines() or ["the evaluation failed"])[-1][:300])
            data = json.loads(proc.stdout[proc.stdout.index("{") :])["bugs"]
            if not data.get("cases"):
                raise RuntimeError(
                    "No usable bug fixes were found in this repository's history (commits that say fix/resolve and "
                    "change 1–4 source files). Try a repository with more history."
                )
            run.result, run.status = summarise(data), "completed"
        except subprocess.TimeoutExpired:
            run.status, run.error = "failed", "Took longer than 20 minutes. Try fewer cases or a smaller repository."
        except Exception as e:
            log.exception("evaluation failed for %s", run.repo_id)
            run.status, run.error = "failed", str(e)[:400]
        run.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def start(db: Session, org_id: str, user_id: str, repo: Repository, cases: int, inline: bool) -> EvalRun:
    path = checkout_path(repo)
    if path is None:
        raise ValueError("Index this repository first so its history is available")
    run = EvalRun(org_id=org_id, repo_id=repo.id, user_id=user_id, cases=max(3, min(cases, MAX_CASES)))
    db.add(run)
    db.commit()
    if inline:
        _execute(run.id, path, run.cases)
        db.refresh(run)
    else:
        t = threading.Thread(target=_execute, args=(run.id, path, run.cases), name="measure", daemon=True)
        _threads[run.id] = t
        t.start()
    return run
