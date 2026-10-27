"""Saved questions: a workspace's own check that answers stay good.

Each question can say which files a good answer must be built from and which words the written answer must contain. A run
asks every question again and records the pass rate, so a change to the model, the skills, the index or the prompts can be
compared with the last run. *Retrieval* runs are free (they only look at what was found); *full* runs also have the model
write each answer and cost tokens. Nothing is sent anywhere that an ordinary question would not be."""

import hashlib
import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_factory
from ..models import GoldenQuestion, GoldenRun, Repository, Skill
from ..telemetry import QUALITY_PASS, QUALITY_RUNS
from . import answer_memory, embedding_state
from . import knowledge as kb
from .access import scope_for_user_id

log = logging.getLogger("repolens.golden")

MAX_QUESTIONS = 100
MAX_FULL = 20  # a full run calls the model once per question
STALE = timedelta(minutes=45)


def _h(x: object) -> str:
    return hashlib.sha256(str(x).encode()).hexdigest()[:10]


def fingerprint(db: Session, org: str) -> dict:
    """The things an answer depends on, each as a short hash, so a change can be named."""
    repos = db.scalars(select(Repository).where(Repository.org_id == org).order_by(Repository.id)).all()
    skills = db.scalars(select(Skill).where(Skill.org_id == org, Skill.status == "approved").order_by(Skill.id)).all()
    return {
        "index": _h([(r.id, r.indexed_sha, r.chunk_count) for r in repos]),
        "skills": _h([(s.id, s.updated_at.timestamp() if s.updated_at else 0) for s in skills]),
        "pipeline": answer_memory.PIPELINE,
        "embedding": _h(embedding_state.current()),
    }


LABEL = {
    "index": "the indexed code or documents",
    "skills": "the skills",
    "pipeline": "how answers are produced",
    "embedding": "the search model",
}


def changed(before: dict, now: dict) -> list[str]:
    return [LABEL[k] for k in LABEL if before.get(k) != now.get(k)]


def check(q: GoldenQuestion, answer: dict | None, files: list[str], full: bool) -> list[dict]:
    """The checks for one question, each with whether it held and why."""
    out = []
    for want in q.expect_files or []:
        ok = any(want.lower() in f.lower() for f in files)
        out.append({"name": f"finds {want}", "ok": ok, "detail": "" if ok else "not among the sources used"})
    if full:
        text = (answer or {}).get("answer", "")
        mode = (answer or {}).get("mode", "")
        for want in q.expect_phrases or []:
            ok = want.lower() in text.lower()
            out.append({"name": f'says "{want}"', "ok": ok, "detail": "" if ok else "missing from the answer"})
        good = mode in ("llm", "extractive")
        out.append({"name": "answers", "ok": good, "detail": "" if good else f"the answer was '{mode or 'empty'}'"})
    elif q.expect_phrases:
        out.append({"name": "phrases", "ok": True, "detail": "not checked in a retrieval run"})
    if not out:
        out.append({"name": "finds sources", "ok": bool(files), "detail": "" if files else "nothing was found"})
    return out


def evaluate(db: Session, run: GoldenRun, questions: list[GoldenQuestion]) -> None:
    scope = scope_for_user_id(db, run.user_id)
    full = run.mode == "full"
    results: list[dict] = []
    for q in questions:
        ids = scope.resolve(q.repo_ids or None) or None
        try:
            if full:
                got = kb.ask(db, run.org_id, q.question, ids, scope=scope, fresh=True)
                files = [s["file"] for s in got.get("sources", [])]
            else:
                got = None
                files = [r["file"] for r in kb.retrieve(db, run.org_id, q.question, ids, 8, scope)]
            checks = check(q, got, files, full)
            row = {
                "id": q.id,
                "question": q.question,
                "passed": all(c["ok"] for c in checks),
                "checks": checks,
                "files": list(dict.fromkeys(files))[:8],
                "answer": (got or {}).get("answer", "")[:400] if full else "",
            }
        except Exception as e:  # one broken question must not stop the run
            row = {
                "id": q.id,
                "question": q.question,
                "passed": False,
                "checks": [
                    {"name": "runs", "ok": False, "detail": str(e).splitlines()[0][:160] if str(e) else "error"}
                ],
                "files": [],
                "answer": "",
            }
        results.append(row)
        run.done, run.results, run.passed = len(results), list(results), sum(1 for r in results if r["passed"])
        db.commit()


def execute(run_id: str) -> None:
    db = session_factory()()
    try:
        run = db.get(GoldenRun, run_id)
        assert run is not None
        try:
            questions = list(
                db.scalars(
                    select(GoldenQuestion)
                    .where(GoldenQuestion.org_id == run.org_id)
                    .order_by(GoldenQuestion.created_at)
                )
            )
            evaluate(db, run, questions)
            run.status = "completed"
            QUALITY_PASS.labels(run.org_id).set(run.passed / max(1, run.total))
        except Exception as e:
            log.exception("golden run failed")
            run.status, run.error = "failed", str(e)[:300]
        run.finished_at = datetime.now(UTC)
        db.commit()
        QUALITY_RUNS.labels(run.trigger, run.status).inc()
    finally:
        db.close()


def running(db: Session, org: str) -> GoldenRun | None:
    cutoff = datetime.now(UTC) - STALE
    for r in db.scalars(select(GoldenRun).where(GoldenRun.org_id == org, GoldenRun.status == "running")):
        started = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=UTC)
        if started < cutoff:  # the server restarted mid-run
            r.status, r.error, r.finished_at = "failed", "The run was interrupted", datetime.now(UTC)
            continue
        return r
    db.commit()
    return None


def _new_run(db: Session, org: str, user_id: str, mode: str, trigger: str) -> GoldenRun:
    total = db.scalar(select(func.count()).select_from(GoldenQuestion).where(GoldenQuestion.org_id == org)) or 0
    run = GoldenRun(
        org_id=org, user_id=user_id, mode=mode, trigger=trigger, total=total, fingerprint=fingerprint(db, org)
    )
    db.add(run)
    db.commit()
    return run


def start(db: Session, org: str, user_id: str, mode: str, inline: bool) -> GoldenRun:
    run = _new_run(db, org, user_id, mode, "manual")
    if inline:
        execute(run.id)
        db.refresh(run)
    else:
        threading.Thread(target=execute, args=(run.id,), name="golden", daemon=True).start()
    return run


# --- Checking the saved questions by itself once an index has changed what answers are built from -------------------

_waiting: dict[str, threading.Timer] = {}  # org id -> the check that will run when indexing goes quiet
_waiting_lock = threading.Lock()


def after_index(org_id: str, delay: float | None = None, timer=threading.Timer) -> None:
    """Called when an index finishes. Several repositories often finish together, so each call pushes the check back and
    only the last one runs it. With inline jobs (tests) it runs at once."""
    s = get_settings()
    if s.run_jobs_inline:
        auto_run(org_id)
        return
    with _waiting_lock:
        old = _waiting.pop(org_id, None)
        if old is not None:
            old.cancel()
        t = timer(s.auto_quality_delay_s if delay is None else delay, auto_run, args=(org_id,))
        t.daemon = True
        _waiting[org_id] = t
        t.start()


def auto_run(org_id: str) -> bool:
    """A free retrieval run of every saved question, unless there is nothing to check or nothing changed since the last
    run. Returns whether one ran."""
    with _waiting_lock:
        _waiting.pop(org_id, None)
    with session_factory()() as db:
        has_questions = db.scalar(
            select(func.count()).select_from(GoldenQuestion).where(GoldenQuestion.org_id == org_id)
        )
        if not has_questions or running(db, org_id):
            return False
        if db.scalar(
            select(func.count())
            .select_from(Repository)
            .where(Repository.org_id == org_id, Repository.status == "indexing")
        ):
            return False  # the index still running will call after_index when it finishes
        last = db.scalar(
            select(GoldenRun)
            .where(GoldenRun.org_id == org_id, GoldenRun.status == "completed")
            .order_by(GoldenRun.started_at.desc())
            .limit(1)
        )
        if last is not None and last.fingerprint == fingerprint(db, org_id):
            return False  # an index that found nothing new changes no answer
        run_id = _new_run(db, org_id, "", "retrieval", "reindex").id
    execute(run_id)
    with session_factory()() as db:
        _report(db, run_id)
    return True


def _report(db: Session, run_id: str) -> None:
    """Say in the log when the automatic run found questions that used to pass and no longer do."""
    run = db.get(GoldenRun, run_id)
    if run is None or run.status != "completed":
        return
    before = db.scalar(
        select(GoldenRun)
        .where(
            GoldenRun.org_id == run.org_id,
            GoldenRun.status == "completed",
            GoldenRun.mode == run.mode,
            GoldenRun.id != run.id,
            GoldenRun.started_at < run.started_at,
        )
        .order_by(GoldenRun.started_at.desc())
        .limit(1)
    )
    worse = compare(_summary(run), _summary(before) if before else None)["broke"]
    if worse:
        log.warning("quality check after re-index: %d of %d saved question(s) got worse", len(worse), run.total)
    else:
        log.info("quality check after re-index: %d of %d saved question(s) passed", run.passed, run.total)


def _summary(r: GoldenRun) -> dict:
    return {"total": r.total, "passed": r.passed, "results": r.results}


def compare(latest: dict | None, previous: dict | None) -> dict:
    """What got better or worse between two completed runs."""
    if not latest or not previous:
        return {"fixed": [], "broke": [], "delta": None}
    was = {r["id"]: r["passed"] for r in previous["results"]}
    return {
        "delta": round(latest["passed"] / max(1, latest["total"]) - previous["passed"] / max(1, previous["total"]), 3),
        "broke": [r["question"] for r in latest["results"] if not r["passed"] and was.get(r["id"])],
        "fixed": [r["question"] for r in latest["results"] if r["passed"] and was.get(r["id"]) is False],
    }
