from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import PersonaRun, Repository, User
from ..services import features
from ..services import personas as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/personas", tags=["personas"])


class RunIn(BaseModel):
    repo_id: str = Field(min_length=1, max_length=32)
    persona: str = Field(min_length=1, max_length=30)
    request: str = Field(min_length=svc.REQUEST_MIN, max_length=svc.REQUEST_MAX)
    fresh: bool = False  # ask the model again even when an earlier run says the same thing


class ShareIn(BaseModel):
    shared: bool


def _people(db: Session, ids: set[str]) -> dict[str, str]:
    rows = db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids))).all() if ids else []
    return {uid: name or email.split("@")[0] for uid, name, email in rows}


def _readable(db: Session, p: Principal, rid: str) -> PersonaRun:
    """A run its author made, or one shared for a repository the reader may open; anything else is "not found", admins
    included."""
    row = db.get(PersonaRun, rid)
    if row is None or row.org_id != p.org_id:
        raise HTTPException(404, "Review not found")
    if row.created_by != p.user_id and not (row.shared and scope_for(db, p).can(row.repo_id)):
        raise HTTPException(404, "Review not found")
    return row


def _short(db: Session, row: PersonaRun, names: dict[str, str], p: Principal, people: dict[str, str]) -> dict:
    findings = row.findings or []
    return {
        "id": row.id,
        "persona": row.persona,
        "persona_name": svc.PERSONAS[row.persona].name if row.persona in svc.PERSONAS else row.persona,
        "title": row.title,
        "status": row.status,
        "repo": names.get(row.repo_id, "a removed repository"),
        "repo_id": row.repo_id,
        "created_at": row.created_at,
        "found": len(findings),
        "high": sum(1 for f in findings if f.get("severity") == "high"),
        "tokens": features.ledger(db, row.org_id, row.id, svc.WORKFLOW),
        "shared": row.shared,
        "mine": row.created_by == p.user_id,
        "author_name": people.get(row.created_by, "a former member"),
    }


def _view(db: Session, row: PersonaRun, p: Principal) -> dict:
    repo = db.get(Repository, row.repo_id)
    return {
        **_short(db, row, {row.repo_id: repo.name} if repo else {}, p, _people(db, {row.created_by})),
        "request": row.request,
        "summary": row.summary,
        "findings": row.findings or [],
        "notes": row.notes or {},
        "model": row.model,
        "error": row.error,
        "base_sha": row.base_sha,
    }


@router.get("")
def personas(p: Principal = Depends(current_principal)):
    return {"personas": svc.listing()}


@router.get("/runs")
def list_runs(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The caller's own reviews and the ones others shared with the workspace."""
    q = (
        select(PersonaRun)
        .where(PersonaRun.org_id == p.org_id, (PersonaRun.created_by == p.user_id) | (PersonaRun.shared.is_(True)))
        .order_by(PersonaRun.created_at.desc())
        .limit(50)
    )
    scope = scope_for(db, p)
    rows = [r for r in db.scalars(q) if scope.can(r.repo_id)]
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    people = _people(db, {r.created_by for r in rows})
    return {"runs": [_short(db, r, names, p, people) for r in rows]}


@router.post("/runs", status_code=201)
def create_run(body: RunIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    repo = db.get(Repository, body.repo_id)
    scope = scope_for(db, p)
    if repo is None or repo.org_id != p.org_id or not scope.can(repo.id):
        raise HTTPException(404, "Repository not found")
    authorize(db, p, "persona.run", repo.name, persona=body.persona)
    try:
        row, reused = svc.run(db, p.org_id, p.user_id, scope, repo, body.persona, body.request, body.fresh)
    except svc.PersonaError as e:
        raise HTTPException(409, str(e)) from e
    return {**_view(db, row, p), "reused": reused}


@router.get("/runs/{rid}")
def get_run(rid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _readable(db, p, rid)
    if not scope_for(db, p).can(row.repo_id):
        raise HTTPException(404, "Review not found")
    return _view(db, row, p)


@router.patch("/runs/{rid}")
def share_run(rid: str, body: ShareIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Share a review with the workspace, or make it private again. Only its author."""
    row = _readable(db, p, rid)
    if row.created_by != p.user_id:
        raise HTTPException(403, "Only the author can change a review")
    authorize(db, p, "persona.run", row.title, change="share" if body.shared else "private")
    row.shared = body.shared
    db.commit()
    return _view(db, row, p)


@router.delete("/runs/{rid}", status_code=204)
def delete_run(rid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The author deletes a review; an admin can also delete a shared one (a private one is not found for them)."""
    row = _readable(db, p, rid)
    if row.created_by != p.user_id and p.role != "admin":
        raise HTTPException(403, "Only the author or an admin can delete a shared review")
    authorize(db, p, "persona.run", row.title, change="delete")
    db.delete(row)
    db.commit()
