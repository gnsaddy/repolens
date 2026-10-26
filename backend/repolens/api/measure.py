from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import EvalRun, Repository
from ..services import measure as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/repositories", tags=["measure"])


class MeasureIn(BaseModel):
    cases: int = Field(10, ge=3, le=svc.MAX_CASES)


def _repo(db: Session, p: Principal, repo_id: str) -> Repository:
    repo = db.get(Repository, repo_id)
    if repo is None or repo.org_id != p.org_id or not scope_for(db, p).can(repo.id):
        raise HTTPException(404, "Repository not found")
    return repo


def _view(r: EvalRun) -> dict:
    return {
        "id": r.id,
        "status": r.status,
        "cases": r.cases,
        "result": r.result or None,
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


@router.post("/{repo_id}/measure", status_code=202)
def measure(repo_id: str, body: MeasureIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Test how well RepoLens finds the files behind this repository's own past bug fixes."""
    repo = _repo(db, p, repo_id)
    if repo.source == "docs":
        raise HTTPException(400, "Only code repositories have a bug-fix history to measure")
    authorize(db, p, "repo.measure", repo.name, cases=body.cases)
    if svc.running(db, p.org_id):
        raise HTTPException(409, "A measurement is already running. Wait for it to finish.")
    try:
        run = svc.start(db, p.org_id, p.user_id, repo, body.cases, get_settings().run_jobs_inline)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return _view(run)


@router.get("/{repo_id}/measure")
def history(
    repo_id: str,
    limit: int = Query(5, ge=1, le=20),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    repo = _repo(db, p, repo_id)
    svc.running(db, p.org_id)  # settles runs that were interrupted
    rows = db.scalars(
        select(EvalRun).where(EvalRun.repo_id == repo.id).order_by(EvalRun.started_at.desc()).limit(limit)
    )
    return {"items": [_view(r) for r in rows], "can_run": svc.checkout_path(repo) is not None and repo.source != "docs"}
