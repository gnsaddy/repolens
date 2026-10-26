from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Repository, Skill
from ..services import skills as svc
from ..services.access import scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/skills", tags=["skills"])

BUILTIN = [
    {
        "name": "walkthrough",
        "title": "Walkthrough for how and why questions",
        "description": "Summary, then the steps in order, each citing a file and function, then what the sources do not cover.",
        "applies": "explain",
    },
    {
        "name": "build-from-conventions",
        "title": "Write things the way your repository does",
        "description": "Checks what already exists, follows your names and imports, marks assumptions, labels general knowledge.",
        "applies": "build",
    },
    {
        "name": "debugging-discipline",
        "title": "Debugging checklist, red flags and optional independent review",
        "description": "Reproduce, localise, reduce, fix the cause, guard, verify.",
        "applies": "investigate",
    },
]


class SkillIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=1000)
    body: str = Field(min_length=10, max_length=svc.MAX_BODY * 2)
    tasks: list[str] = Field(default_factory=list, max_length=4)
    repo_ids: list[str] = Field(default_factory=list, max_length=50)
    always: bool = False


class SkillPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    body: str | None = Field(default=None, min_length=10, max_length=svc.MAX_BODY * 2)
    tasks: list[str] | None = Field(default=None, max_length=4)
    repo_ids: list[str] | None = Field(default=None, max_length=50)
    always: bool | None = None


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject|disable|enable)$")
    note: str = Field(default="", max_length=500)


def _view(s: Skill, p: Principal, names: dict[str, str]) -> dict:
    mine = s.created_by == p.user_id
    return {
        "id": s.id,
        "name": s.name,
        "title": s.title,
        "description": s.description,
        "body": s.body,
        "tasks": s.tasks or [],
        "repo_ids": s.repo_ids or [],
        "repos": [names.get(r, "") for r in (s.repo_ids or []) if r in names],
        "always": s.always,
        "status": s.status,
        "note": s.note,
        "uses": s.uses,
        "mine": mine,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _check_tasks(tasks: list[str]) -> list[str]:
    bad = [t for t in tasks if t not in svc.TASKS]
    if bad:
        raise HTTPException(422, f"Unknown task type: {', '.join(bad)} (use {', '.join(svc.TASKS)})")
    return list(dict.fromkeys(tasks))


def _repos(db: Session, p: Principal, ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(ids))
    if ids:
        found = set(db.scalars(select(Repository.id).where(Repository.org_id == p.org_id, Repository.id.in_(ids))))
        if found != set(ids) or not scope_for(db, p).can_all(ids):
            raise HTTPException(404, "Repository not found")
    return ids


def _own(db: Session, p: Principal, skill_id: str) -> Skill:
    s = db.get(Skill, skill_id)
    if s is None or s.org_id != p.org_id:
        raise HTTPException(404, "Skill not found")  # another organisation's skill looks exactly like a missing one
    return s


def _visible(s: Skill, p: Principal) -> bool:
    return p.role == "admin" or s.status == "approved" or s.created_by == p.user_id


@router.get("")
def list_skills(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Approved skills for everyone; your own submissions and, for admins, everything (including what awaits review)."""
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    rows = db.scalars(select(Skill).where(Skill.org_id == p.org_id).order_by(Skill.status, Skill.title))
    items = [_view(s, p, names) for s in rows if _visible(s, p)]
    return {"items": items, "builtin": BUILTIN, "pending": sum(1 for s in items if s["status"] == "pending")}


@router.post("", status_code=201)
def submit(body: SkillIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Members submit; an admin's submission is approved straight away."""
    authorize(db, p, "skill.submit", body.title)
    if (db.scalar(select(func.count()).select_from(Skill).where(Skill.org_id == p.org_id)) or 0) >= svc.MAX_PER_ORG:
        raise HTTPException(409, f"A workspace can have {svc.MAX_PER_ORG} skills. Delete some you no longer use.")
    name = svc.slug(body.title)
    taken = {n for n in db.scalars(select(Skill.name).where(Skill.org_id == p.org_id))}
    base, n = name, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    admin = p.role == "admin"
    s = Skill(
        org_id=p.org_id,
        name=name,
        title=svc.clean(body.title, 200),
        description=svc.clean(body.description, 1000),
        body=svc.clean(body.body, svc.MAX_BODY),
        tasks=_check_tasks(body.tasks),
        repo_ids=_repos(db, p, body.repo_ids),
        always=body.always,
        status="approved" if admin else "pending",
        created_by=p.user_id,
        reviewed_by=p.user_id if admin else "",
    )
    db.add(s)
    db.commit()
    return _view(s, p, {})


@router.patch("/{skill_id}")
def edit(skill_id: str, body: SkillPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Admins edit anything. The author may edit their own skill until it is approved; an edit sends it back for review."""
    s = _own(db, p, skill_id)
    if not _visible(s, p):
        raise HTTPException(404, "Skill not found")
    admin = p.role == "admin"
    if not admin and (s.created_by != p.user_id or s.status == "approved"):
        raise HTTPException(403, "Only an admin can change an approved skill")
    authorize(db, p, "skill.submit", s.title, change="edit")
    data = body.model_dump(exclude_unset=True)
    for k in ("title", "description", "body"):
        if data.get(k) is not None:
            setattr(s, k, svc.clean(data[k], {"title": 200, "description": 1000, "body": svc.MAX_BODY}[k]))
    if data.get("tasks") is not None:
        s.tasks = _check_tasks(data["tasks"])
    if data.get("repo_ids") is not None:
        s.repo_ids = _repos(db, p, data["repo_ids"])
    if data.get("always") is not None:
        s.always = data["always"]
    if not admin:
        s.status, s.note = "pending", ""
    db.commit()
    return _view(s, p, {})


@router.post("/{skill_id}/review")
def review(skill_id: str, body: Decision, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s = _own(db, p, skill_id)
    authorize(db, p, "skill.review", s.title, decision=body.decision)
    s.status = {"approve": "approved", "enable": "approved", "reject": "rejected", "disable": "disabled"}[body.decision]
    s.note = body.note if body.decision == "reject" else ""
    s.reviewed_by = p.user_id
    s.updated_at = datetime.now(UTC)
    db.commit()
    return _view(s, p, {})


@router.delete("/{skill_id}", status_code=204)
def delete(skill_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    s = _own(db, p, skill_id)
    if not _visible(s, p):
        raise HTTPException(404, "Skill not found")
    if p.role != "admin" and (s.created_by != p.user_id or s.status == "approved"):
        raise HTTPException(403, "Only an admin can delete an approved skill")
    authorize(db, p, "skill.submit", s.title, change="delete")
    db.delete(s)
    db.commit()
