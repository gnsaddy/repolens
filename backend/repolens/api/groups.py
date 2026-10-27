from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import GroupOverview, RepoGroup, User
from ..security import redact_stored
from ..services import groups as svc
from ..services import repo_links as lk
from ..services.access import Scope, scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/groups", tags=["systems"])


class GroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    repo_ids: list[str] = Field(min_length=1, max_length=svc.MAX_REPOS)


def _people(db: Session, ids: set[str]) -> dict[str, str]:
    """Who made things, by name (the part of the address before the @ when someone has no name)."""
    rows = db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids))).all() if ids else []
    return {uid: name or email.split("@")[0] for uid, name, email in rows}


def _get(db: Session, p: Principal, gid: str) -> RepoGroup:
    g = db.get(RepoGroup, gid)
    if g is None or g.org_id != p.org_id:
        raise HTTPException(404, "System not found")
    return g


def _view(g: RepoGroup, scope: Scope, people: dict[str, str]) -> dict:
    """A group as one person may see it: repositories they cannot see are counted, never named."""
    shown = [i for i in g.repo_ids if scope.can(i)]
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "repo_ids": shown,
        "hidden": len(g.repo_ids) - len(shown),
        "created_by": g.created_by,
        "created_by_name": people.get(g.created_by, "a former member"),
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


def _check_name(db: Session, p: Principal, name: str, keep: str = "") -> str:
    name = redact_stored(" ".join(name.split()))[0]
    clash = db.scalar(
        select(RepoGroup.id).where(RepoGroup.org_id == p.org_id, func.lower(RepoGroup.name) == name.lower())
    )
    if clash and clash != keep:
        raise HTTPException(409, "A system with this name already exists")
    return name


@router.get("")
def list_groups(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    rows = list(db.scalars(select(RepoGroup).where(RepoGroup.org_id == p.org_id).order_by(RepoGroup.name)))
    people = _people(db, {g.created_by for g in rows})
    return {"groups": [_view(g, scope, people) for g in rows], "max_repos": svc.MAX_REPOS}


@router.post("", status_code=201)
def create_group(body: GroupIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "group.write", body.name[:80])
    if (
        db.scalar(select(func.count()).select_from(RepoGroup).where(RepoGroup.org_id == p.org_id)) or 0
    ) >= svc.MAX_GROUPS:
        raise HTTPException(409, f"A workspace can keep {svc.MAX_GROUPS} systems. Delete some you no longer need.")
    scope = scope_for(db, p)
    ids = svc.clean_ids(db, p.org_id, scope, body.repo_ids)
    g = RepoGroup(
        org_id=p.org_id,
        name=_check_name(db, p, body.name),
        description=redact_stored(body.description.strip())[0],
        repo_ids=ids,
        created_by=p.user_id,
    )
    db.add(g)
    db.commit()
    return _view(g, scope, _people(db, {g.created_by}))


def _may_change(g: RepoGroup, p: Principal) -> None:
    if p.role != "admin" and g.created_by != p.user_id:
        raise HTTPException(403, "Only an admin or the person who saved this system can change it")


@router.put("/{gid}")
def update_group(gid: str, body: GroupIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    g = _get(db, p, gid)
    _may_change(g, p)
    authorize(db, p, "group.write", g.name, change="update")
    scope = scope_for(db, p)
    ids = svc.clean_ids(db, p.org_id, scope, body.repo_ids)
    hidden = [i for i in g.repo_ids if not scope.can(i)]  # repositories this person cannot see stay in the system
    g.name = _check_name(db, p, body.name, keep=g.id)
    g.description = redact_stored(body.description.strip())[0]
    g.repo_ids = ids + [i for i in hidden if i not in ids]
    db.commit()
    return _view(g, scope, _people(db, {g.created_by}))


@router.delete("/{gid}", status_code=204)
def delete_group(gid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    g = _get(db, p, gid)
    _may_change(g, p)
    authorize(db, p, "group.write", g.name, change="delete")
    db.delete(g)
    db.commit()


@router.get("/analysis")
def analyse_selection(
    repo_ids: str = Query(min_length=1, max_length=1200),
    days: int = Query(svc.WINDOW_DAYS, ge=7, le=365),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """The same picture for repositories picked on the page and not saved (comma-separated ids)."""
    ids = svc.clean_ids(db, p.org_id, scope_for(db, p), [x for x in repo_ids.split(",") if x])
    return svc.analyse(db, p.org_id, ids, days)


class AskIn(BaseModel):
    repo_ids: list[str] = Field(min_length=1, max_length=svc.MAX_REPOS)
    question: str = Field(default="", max_length=1000)
    src: str | None = Field(default=None, max_length=64)  # explain the link from this repository ...
    dst: str | None = Field(default=None, max_length=64)  # ... to this one
    node_id: str | None = Field(default=None, max_length=64)  # or what depends on this class, function or file
    fresh: bool = False


@router.post("/ask")
def ask(body: AskIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Ask about the chosen repositories: answered from their code and documents and from the dependency analysis."""
    if len(body.question.strip()) < 3 and not (body.src and body.dst) and not body.node_id:
        raise HTTPException(422, "Ask a question, or choose a link or a class to explain")
    authorize(db, p, "group.ask", body.question[:80])
    scope = scope_for(db, p)
    ids = svc.clean_ids(db, p.org_id, scope, body.repo_ids)
    return svc.ask(db, p.org_id, scope, ids, body.question, body.src, body.dst, body.node_id, body.fresh)


@router.get("/impact")
def impact(
    node_id: str = Query(min_length=1, max_length=64),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """What in other repositories depends on this class, function or file, and who to ask about it."""
    out = lk.impact(db, p.org_id, scope_for(db, p), node_id)
    if out is None:
        raise HTTPException(404, "Not found")
    return out


@router.get("/surface")
def surface(
    repo_id: str = Query(min_length=1, max_length=64),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """What other repositories use from this one, most used first, and what its package offers that nobody uses."""
    out = lk.surface(db, p.org_id, scope_for(db, p), repo_id)
    if out is None:
        raise HTTPException(404, "Repository not found")
    return out


@router.get("/changes")
def changes(
    repo_ids: str = Query(min_length=1, max_length=1200),
    days: int = Query(7, ge=1, le=365),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Dependencies between the chosen repositories that appeared or disappeared in the last days."""
    scope = scope_for(db, p)
    ids = svc.clean_ids(db, p.org_id, scope, [x for x in repo_ids.split(",") if x])
    return lk.changes(db, p.org_id, scope, ids, days)


@router.get("/code-graph")
def code_graph(
    repo_ids: str = Query(min_length=1, max_length=1200),
    src: str | None = Query(None, max_length=64),
    dst: str | None = Query(None, max_length=64),
    weak: bool = False,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """The classes, functions and files on both ends of each link between the chosen repositories, ready to draw.
    `src` and `dst` narrow it to one direction of one pair; `weak` adds calls matched by name alone."""
    ids = svc.clean_ids(db, p.org_id, scope_for(db, p), [x for x in repo_ids.split(",") if x])
    pair = (src, dst) if src and dst and src in ids and dst in ids else None
    return svc.code_graph(db, p.org_id, ids, pair, weak)


@router.get("/{gid}/analysis")
def analyse_group(
    gid: str,
    days: int = Query(svc.WINDOW_DAYS, ge=7, le=365),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    g = _get(db, p, gid)
    shown = [i for i in g.repo_ids if scope_for(db, p).can(i)]
    return svc.analyse(db, p.org_id, shown, days)


def _overview_view(o: GroupOverview, scope: Scope, group_ids: list[str], people: dict[str, str]) -> dict:
    mine = [i for i in group_ids if scope.can(i)]
    return {
        "id": o.id,
        "content": o.content,
        "mode": o.mode,
        "model": o.model,
        "unsupported_names": o.unsupported_names or [],
        "created_by": o.created_by,
        "created_by_name": people.get(o.created_by, "a former member"),
        "created_at": o.created_at,
        "repositories": len(o.repo_ids),
        "partial": set(o.repo_ids) != set(mine),  # built from fewer repositories than this system has now
    }


@router.get("/{gid}/overviews")
def overviews(gid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Written overviews, newest first. One built from a repository this person cannot see is left out."""
    g = _get(db, p, gid)
    scope = scope_for(db, p)
    rows = db.scalars(
        select(GroupOverview)
        .where(GroupOverview.group_id == g.id)
        .order_by(GroupOverview.created_at.desc(), GroupOverview.id)
    ).all()
    people = _people(db, {o.created_by for o in rows})
    seen = [o for o in rows if scope.can_all(o.repo_ids)]
    return {
        "overviews": [_overview_view(o, scope, g.repo_ids, people) for o in seen],
        "hidden": len(rows) - len(seen),
    }


@router.post("/{gid}/overview", status_code=201)
def write_overview(gid: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Write an overview of this system from what is known about its repositories. Uses the model once."""
    g = _get(db, p, gid)
    authorize(db, p, "group.overview", g.name)
    scope = scope_for(db, p)
    ids = [i for i in g.repo_ids if scope.can(i)]
    if not ids:
        raise HTTPException(404, "Repository not found")
    o = svc.generate(db, p.org_id, p.user_id, g, ids)
    return _overview_view(o, scope, g.repo_ids, _people(db, {o.created_by}))
