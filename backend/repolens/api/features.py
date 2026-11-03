from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import FeatureDraft, Repository, User
from ..services import features as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/features", tags=["features"])


class DraftIn(BaseModel):
    repo_id: str = Field(min_length=1, max_length=32)
    request: str = Field(min_length=svc.REQUEST_MIN, max_length=svc.REQUEST_MAX)
    fresh: bool = False  # ask the model again even when an earlier draft says the same thing


def _readable(db: Session, p: Principal, fid: str) -> FeatureDraft:
    """A draft its author wrote, or one shared with the workspace for a repository the reader may open. Anything else
    is "not found", admins included: a private draft is private."""
    row = db.get(FeatureDraft, fid)
    if row is None or row.org_id != p.org_id:
        raise HTTPException(404, "Draft not found")
    if row.created_by != p.user_id and not (row.shared and scope_for(db, p).can(row.repo_id)):
        raise HTTPException(404, "Draft not found")
    return row


def _author(db: Session, p: Principal, fid: str) -> FeatureDraft:
    row = _readable(db, p, fid)
    if row.created_by != p.user_id:
        raise HTTPException(403, "Only the author can change a draft")
    return row


def _short(db: Session, row: FeatureDraft, names: dict[str, str], p: Principal, people: dict[str, str]) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "repo": names.get(row.repo_id, "a removed repository"),
        "repo_id": row.repo_id,
        "created_at": row.created_at,
        "ok": (row.review or {}).get("ok"),
        "tokens": svc.ledger(db, row.org_id, row.id),
        "shared": row.shared,
        "mine": row.created_by == p.user_id,
        "author_name": people.get(row.created_by, "a former member"),
    }


def _people(db: Session, ids: set[str]) -> dict[str, str]:
    rows = db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids))).all() if ids else []
    return {uid: name or email.split("@")[0] for uid, name, email in rows}


def _view(db: Session, row: FeatureDraft, p: Principal) -> dict:
    repo = db.get(Repository, row.repo_id)
    return {
        **_short(db, row, {row.repo_id: repo.name} if repo else {}, p, _people(db, {row.created_by})),
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
    """The caller's own drafts and the ones others shared with the workspace."""
    q = (
        select(FeatureDraft)
        .where(
            FeatureDraft.org_id == p.org_id,
            (FeatureDraft.created_by == p.user_id) | (FeatureDraft.shared.is_(True)),
        )
        .order_by(FeatureDraft.created_at.desc())
        .limit(50)
    )
    scope = scope_for(db, p)
    rows = [r for r in db.scalars(q) if r.created_by == p.user_id or scope.can(r.repo_id)]
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    people = _people(db, {r.created_by for r in rows})
    return {"drafts": [_short(db, r, names, p, people) for r in rows if scope.can(r.repo_id)]}


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
    return {**_view(db, row, p), "reused": reused}


@router.get("/{fid}")
def get_draft(fid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _readable(db, p, fid)
    if not scope_for(db, p).can(row.repo_id):
        raise HTTPException(404, "Draft not found")
    return _view(db, row, p)


class ShareIn(BaseModel):
    shared: bool


@router.patch("/{fid}")
def share_draft(fid: str, body: ShareIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Share a draft with the workspace, or make it private again. Only its author."""
    row = _author(db, p, fid)
    authorize(db, p, "feature.draft", row.title, change="share" if body.shared else "private")
    row.shared = body.shared
    db.commit()
    return _view(db, row, p)


@router.delete("/{fid}", status_code=204)
def delete_draft(fid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The author deletes a draft; an admin can also delete a shared one (a private one is not found for them)."""
    row = _readable(db, p, fid)
    if row.created_by != p.user_id and p.role != "admin":
        raise HTTPException(403, "Only the author or an admin can delete a shared draft")
    authorize(db, p, "feature.draft", row.title, change="delete")
    db.delete(row)
    db.commit()
