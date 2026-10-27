from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import GoldenQuestion, GoldenRun, Repository
from ..security import redact_stored
from ..services import golden as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/golden", tags=["quality"])


class QuestionIn(BaseModel):
    question: str = Field(min_length=5, max_length=1000)
    expect_files: list[str] = Field(default_factory=list, max_length=10)
    expect_phrases: list[str] = Field(default_factory=list, max_length=10)
    repo_ids: list[str] = Field(default_factory=list, max_length=20)


class RunIn(BaseModel):
    mode: str = Field(default="retrieval", pattern="^(retrieval|full)$")


def _lines(items: list[str]) -> list[str]:
    return list(dict.fromkeys(redact_stored(x.strip())[0][:200] for x in items if x.strip()))[:10]


def _run_view(r: GoldenRun, full: bool = True) -> dict:
    return {
        "id": r.id,
        "mode": r.mode,
        "trigger": r.trigger,
        "status": r.status,
        "total": r.total,
        "done": r.done,
        "passed": r.passed,
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "fingerprint": r.fingerprint,
        **({"results": r.results} if full else {}),
    }


def _visible(r: GoldenRun, p: Principal) -> bool:
    """A run records which files were found under its runner's access, so it is shown to them and to admins."""
    return p.role == "admin" or r.user_id == p.user_id


@router.get("")
def overview(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    svc.running(db, p.org_id)
    qs = db.scalars(select(GoldenQuestion).where(GoldenQuestion.org_id == p.org_id).order_by(GoldenQuestion.created_at))
    runs = [
        r
        for r in db.scalars(
            select(GoldenRun).where(GoldenRun.org_id == p.org_id).order_by(GoldenRun.started_at.desc()).limit(30)
        )
        if _visible(r, p)
    ]
    done = [r for r in runs if r.status == "completed"]
    latest = done[0] if done else None
    previous = next((r for r in done[1:] if r.mode == latest.mode), None) if latest else None
    now = svc.fingerprint(db, p.org_id)
    results = {x["id"]: x for r in done[:1] for x in r.results}
    return {
        "questions": [
            {
                "id": q.id,
                "question": q.question,
                "expect_files": q.expect_files or [],
                "expect_phrases": q.expect_phrases or [],
                "repo_ids": q.repo_ids or [],
                "mine": q.created_by == p.user_id,
                "last": results.get(q.id),
            }
            for q in qs
        ],
        "runs": [_run_view(r, full=False) for r in runs[:10]],
        "latest": _run_view(latest) if latest else None,
        "compare": svc.compare(_run_view(latest) if latest else None, _run_view(previous) if previous else None),
        "changed": svc.changed(latest.fingerprint, now) if latest else [],
        "active": next((_run_view(r) for r in runs if r.status == "running"), None),
        "max_full": svc.MAX_FULL,
    }


@router.post("/questions", status_code=201)
def add_question(body: QuestionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "golden.write", body.question[:80])
    if (
        db.scalar(select(func.count()).select_from(GoldenQuestion).where(GoldenQuestion.org_id == p.org_id)) or 0
    ) >= svc.MAX_QUESTIONS:
        raise HTTPException(
            409, f"A workspace can keep {svc.MAX_QUESTIONS} saved questions. Delete some you no longer need."
        )
    ids = list(dict.fromkeys(body.repo_ids))
    if ids:
        found = set(db.scalars(select(Repository.id).where(Repository.org_id == p.org_id, Repository.id.in_(ids))))
        if found != set(ids) or not scope_for(db, p).can_all(ids):
            raise HTTPException(404, "Repository not found")
    q = GoldenQuestion(
        org_id=p.org_id,
        question=redact_stored(body.question.strip())[0],
        expect_files=_lines(body.expect_files),
        expect_phrases=_lines(body.expect_phrases),
        repo_ids=ids,
        created_by=p.user_id,
    )
    db.add(q)
    db.commit()
    return {"id": q.id}


@router.delete("/questions/{qid}", status_code=204)
def delete_question(qid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = db.get(GoldenQuestion, qid)
    if q is None or q.org_id != p.org_id:
        raise HTTPException(404, "Question not found")
    if p.role != "admin" and q.created_by != p.user_id:
        raise HTTPException(403, "Only an admin can delete someone else's saved question")
    authorize(db, p, "golden.write", q.question[:80], change="delete")
    db.delete(q)
    db.commit()


@router.post("/run", status_code=202)
def run(body: RunIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Ask every saved question again and record the pass rate. 'full' also has the model write each answer."""
    authorize(db, p, "golden.run", body.mode)
    total = db.scalar(select(func.count()).select_from(GoldenQuestion).where(GoldenQuestion.org_id == p.org_id)) or 0
    if not total:
        raise HTTPException(409, "Save at least one question first")
    if body.mode == "full" and total > svc.MAX_FULL:
        raise HTTPException(
            409,
            f"A full run asks the model {total} times. Limit: {svc.MAX_FULL} questions. Use a retrieval run, or delete some questions.",
        )
    if svc.running(db, p.org_id):
        raise HTTPException(409, "A run is already in progress")
    return _run_view(svc.start(db, p.org_id, p.user_id, body.mode, get_settings().run_jobs_inline))


@router.get("/runs/{rid}")
def get_run(rid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = db.get(GoldenRun, rid)
    if r is None or r.org_id != p.org_id or not _visible(r, p):
        raise HTTPException(404, "Run not found")
    return _run_view(r)
