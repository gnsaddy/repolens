from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import FeatureDraft, Repository
from ..services import features as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/features", tags=["features"])


class DraftIn(BaseModel):
    repo_id: str = Field(min_length=1, max_length=32)
    request: str = Field(min_length=svc.REQUEST_MIN, max_length=svc.REQUEST_MAX)
    fresh: bool = False  # ask the model again even when an earlier draft says the same thing


def _own(db: Session, p: Principal, fid: str) -> FeatureDraft:
    row = db.get(FeatureDraft, fid)
    if row is None or row.org_id != p.org_id or (row.created_by != p.user_id and p.role != "admin"):
        raise HTTPException(404, "Draft not found")  # drafts are private to their author (and admins)
    return row


def _short(db: Session, row: FeatureDraft, names: dict[str, str]) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "repo": names.get(row.repo_id, "a removed repository"),
        "repo_id": row.repo_id,
        "created_at": row.created_at,
        "ok": (row.review or {}).get("ok"),
        "tokens": svc.ledger(db, row.org_id, row.id),
    }


def _view(db: Session, row: FeatureDraft) -> dict:
    repo = db.get(Repository, row.repo_id)
    return {
        **_short(db, row, {row.repo_id: repo.name} if repo else {}),
        "request": row.request,
        "spec": row.spec,
        "impact": row.impact or [],
        "diff": row.diff,
        "new_files": row.new_files or [],
        "review": row.review or {},
        "pr_title": row.pr_title,
        "pr_body": row.pr_body,
        "model": row.model,
        "error": row.error,
        "base_sha": row.base_sha,
        "note": "A draft is text only. Nothing was applied, branched or pushed. Copy it where you want it and test it.",
    }


@router.get("")
def list_drafts(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(FeatureDraft).where(FeatureDraft.org_id == p.org_id).order_by(FeatureDraft.created_at.desc()).limit(50)
    if p.role != "admin":
        q = q.where(FeatureDraft.created_by == p.user_id)
    rows = list(db.scalars(q))
    scope = scope_for(db, p)
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    return {"drafts": [_short(db, r, names) for r in rows if scope.can(r.repo_id)]}


@router.post("", status_code=201)
def create_draft(body: DraftIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, body.repo_id)
    scope = scope_for(db, p)
    if repo is None or repo.org_id != p.org_id or not scope.can(repo.id):
        raise HTTPException(404, "Repository not found")
    authorize(db, p, "feature.draft", repo.name)
    try:
        row, reused = svc.draft(db, p.org_id, p.user_id, scope, repo, body.request, body.fresh)
    except svc.FeatureError as e:
        raise HTTPException(409, str(e)) from e
    return {**_view(db, row), "reused": reused}


@router.get("/{fid}")
def get_draft(fid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _own(db, p, fid)
    if not scope_for(db, p).can(row.repo_id):
        raise HTTPException(404, "Draft not found")
    return _view(db, row)


@router.delete("/{fid}", status_code=204)
def delete_draft(fid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _own(db, p, fid)
    authorize(db, p, "feature.draft", row.title, change="delete")
    db.delete(row)
    db.commit()
