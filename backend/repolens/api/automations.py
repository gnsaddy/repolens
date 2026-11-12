"""Automations: turn on work that starts by itself (investigate a CI failure, review a new pull request),
see which model does it, and see what it did."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import AgentDraft, AgentRule, AgentRun, AgentSetting, Repository, User
from ..services import agent_controls as controls
from ..services import automations as svc
from ..services.github import GitHubError
from ..services.policy import authorize

router = APIRouter(prefix="/api/automations", tags=["automations"])


class RuleIn(BaseModel):
    enabled: bool = False
    daily_limit: int = Field(default=5, ge=1, le=50)
    all_branches: bool = False
    re_review: bool = False
    event: str = Field(default=svc.CI, pattern="^(ci_failure|pr_review|drift_draft|watch)$")


def _rule_view(r: AgentRule | None) -> dict:
    if r is None:
        return {"enabled": False, "daily_limit": 5, "all_branches": False, "re_review": False}
    return {
        "enabled": r.enabled,
        "daily_limit": r.daily_limit,
        "all_branches": r.all_branches,
        "re_review": r.re_review,
    }


@router.get("")
def overview(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The model in use, each event's rules (the workspace rule and one per repository), and the latest runs."""
    authorize(db, p, "automation.read", "overview")
    repos = db.scalars(
        select(Repository).where(Repository.org_id == p.org_id, Repository.source == "git").order_by(Repository.name)
    ).all()
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    rules = {(r.event, r.repo_id): r for r in db.scalars(select(AgentRule).where(AgentRule.org_id == p.org_id))}
    events = {}
    for ev in svc.EVENTS:
        events[ev] = {
            "all": _rule_view(rules.get((ev, ""))),
            "repositories": [
                {"id": r.id, "name": r.name, **_rule_view(rules.get((ev, r.id))), "own_rule": (ev, r.id) in rules}
                for r in repos
            ],
            "used_today": svc.used_today(db, p.org_id, ev),
        }
    runs = db.scalars(
        select(AgentRun).where(AgentRun.org_id == p.org_id).order_by(AgentRun.created_at.desc()).limit(30)
    ).all()
    shown = svc.describe_runs(db, list(runs))
    for row in shown:
        row["repo"] = names.get(row.pop("repo_id"), "")
    health = controls.health(db, p.org_id)
    return {"model": svc.model_info(db, p.org_id), "events": events, "runs": shown, "controls": health}


class ControlsIn(BaseModel):
    paused: bool = False
    token_budget: int = Field(default=0, ge=0, le=100_000_000)  # tokens a day the agent may use; 0 = no limit


@router.put("/controls")
def set_controls(body: ControlsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Pause the agent (nothing starts by itself) and give it a daily token budget. An admin's own runs go ahead."""
    authorize(db, p, "automation.configure", "controls", paused=body.paused, token_budget=body.token_budget)
    row = controls.setting(db, p.org_id)
    if row is None:
        row = AgentSetting(org_id=p.org_id)
        db.add(row)
    row.paused, row.token_budget, row.updated_by = body.paused, body.token_budget, p.user_id
    row.updated_at = datetime.now(UTC)
    db.commit()
    return controls.health(db, p.org_id)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Stop a run that is going on. Its work ends at the next step and what it drafted so far is dismissed."""
    run = db.get(AgentRun, run_id)
    if run is None or run.org_id != p.org_id:
        raise HTTPException(404, "Not found")
    authorize(db, p, "automation.run", run_id, how="cancel")
    try:
        controls.cancel(db, run, p.user_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {"id": run.id, "status": run.status, "reason": run.reason}


@router.put("/rules/{target}")
def set_rule(target: str, body: RuleIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """`target` is a repository id, or `all` for every repository. The work later runs as the admin who saves this."""
    repo_id = "" if target == "all" else target
    if repo_id:
        repo = db.get(Repository, repo_id)
        if repo is None or repo.org_id != p.org_id or repo.source != "git":
            raise HTTPException(404, "Repository not found")
    authorize(
        db,
        p,
        "automation.configure",
        target,
        event=body.event,
        enabled=body.enabled,
        daily_limit=body.daily_limit,
        all_branches=body.all_branches,
    )
    rule = db.scalar(
        select(AgentRule).where(
            AgentRule.org_id == p.org_id, AgentRule.event == body.event, AgentRule.repo_id == repo_id
        )
    )
    if rule is None:
        rule = AgentRule(org_id=p.org_id, repo_id=repo_id, event=body.event)
        db.add(rule)
    rule.enabled, rule.daily_limit, rule.all_branches = body.enabled, body.daily_limit, body.all_branches
    rule.re_review = body.re_review and body.event == svc.PR
    rule.created_by = p.user_id
    rule.updated_at = datetime.now(UTC)
    db.commit()
    return _rule_view(rule)


# ------------------------------------------------------------------------------------------------ the inbox
class DraftPatch(BaseModel):
    status: str = Field(pattern="^(new|read|dismissed|archived|restore)$")


class ApproveIn(BaseModel):
    body: str | None = Field(default=None, max_length=svc.MAX_COMMENT)  # the comment as edited; none = as drafted


def _draft_view(d: AgentDraft, run: dict | None, repo: str, decided_by: str = "") -> dict:
    return {
        "id": d.id,
        "kind": d.kind,
        "pending": bool((d.meta or {}).get("pending")),
        "title": d.title,
        "body": d.body,
        "status": d.status,
        "error": d.error,
        "posted_url": d.posted_url,
        "repo": repo,
        "link": (d.meta or {}).get("link", ""),
        "created_at": d.created_at,
        "decided_at": d.decided_at,
        "decided_by": decided_by,
        "investigations": (run or {}).get("investigations", []),
        "reviews": (run or {}).get("reviews", []),
        "files": (run or {}).get("files", []),
    }


@router.post("/runs/{run_id}/retry")
def retry(run_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Try a run again, run a skipped one anyway, or review a pull request again."""
    run = db.get(AgentRun, run_id)
    if run is None or run.org_id != p.org_id:
        raise HTTPException(404, "Not found")
    authorize(db, p, "automation.run", run_id, how=svc.retry_kind(run) or "")
    try:
        svc.retry_run(db, run, p.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.refresh(run)
    return {"id": run.id, "status": run.status, "reason": run.reason}


@router.get("/summary")
def summary(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The agent at a glance for the Dashboard and Insights (admins)."""
    authorize(db, p, "automation.read", "summary")
    return controls.summary(db, p.org_id)


TABS = {
    "open": ("new", "read"),  # waiting for a person
    "posted": ("posted",),  # put on GitHub after an approval
    "archived": ("archived",),  # set aside by a person; a draft the agent replaces or cancels is removed, not kept
}


@router.get("/inbox")
def inbox(status: str = "open", p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What the agent made. `open` is waiting, `posted` is what was put on GitHub, `archived` is what was set aside."""
    authorize(db, p, "automation.inbox", "list")
    counts = {
        tab: db.scalar(
            select(func.count())
            .select_from(AgentDraft)
            .where(AgentDraft.org_id == p.org_id, AgentDraft.status.in_(states))
        )
        or 0
        for tab, states in TABS.items()
    }
    q = select(AgentDraft).where(AgentDraft.org_id == p.org_id)
    if status in TABS:
        q = q.where(AgentDraft.status.in_(TABS[status]))
    order = AgentDraft.decided_at.desc() if status in ("posted", "archived") else AgentDraft.created_at.desc()
    drafts = db.scalars(q.order_by(order, AgentDraft.created_at.desc()).limit(50)).all()
    runs = (
        {r.id: r for r in db.scalars(select(AgentRun).where(AgentRun.id.in_([d.run_id for d in drafts])))}
        if drafts
        else {}
    )
    described = {r["id"]: r for r in svc.describe_runs(db, list(runs.values()))}
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    who = (
        {
            u.id: (u.name or u.email)
            for u in db.scalars(select(User).where(User.id.in_([d.decided_by for d in drafts if d.decided_by])))
        }
        if drafts
        else {}
    )
    return {
        "counts": counts,
        "drafts": [
            _draft_view(d, described.get(d.run_id), names.get(d.repo_id, ""), who.get(d.decided_by, "")) for d in drafts
        ],
    }


def _own_draft(db: Session, p: Principal, draft_id: str) -> AgentDraft:
    d = db.get(AgentDraft, draft_id)
    if d is None or d.org_id != p.org_id:
        raise HTTPException(404, "Not found")
    return d


@router.patch("/inbox/{draft_id}")
def mark(draft_id: str, body: DraftPatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Mark read, archive, or restore. A posted comment can only be archived (it stays on GitHub); restoring an archived
    comment that was posted puts it back under Posted, and any other archived draft back to waiting."""
    d = _own_draft(db, p, draft_id)
    authorize(db, p, "automation.inbox", draft_id, status=body.status)
    if d.status == "posted" and body.status != "archived":
        raise HTTPException(409, "It was posted already")
    status = "archived" if body.status == "dismissed" else body.status  # "dismissed" is the old name for archive
    if status == "restore":
        if d.status != "archived":
            raise HTTPException(409, "Only an archived draft can be restored")
        d.status = "posted" if d.posted_url else "new"
    elif status == "archived":
        if d.status == "archived":
            raise HTTPException(409, "It is archived already")
        d.status = "archived"
        if not d.posted_url:  # who set a draft aside; a posted comment keeps who posted it
            d.decided_by, d.decided_at = p.user_id, datetime.now(UTC)
    else:
        d.status = status
        if not d.posted_url:
            d.decided_by, d.decided_at = "", None
    db.commit()
    return {"id": d.id, "status": d.status}


@router.post("/inbox/{draft_id}/approve")
def approve(draft_id: str, body: ApproveIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Post a drafted comment on its pull request. Nothing else in the agent writes to GitHub."""
    d = _own_draft(db, p, draft_id)
    authorize(db, p, "automation.post", draft_id)
    if d.status == "posted":
        raise HTTPException(409, "It was posted already")
    try:
        svc.post_draft(db, d, p.user_id, body.body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except GitHubError as e:
        raise HTTPException(502, str(e)) from None
    return {"id": d.id, "status": d.status, "posted_url": d.posted_url}
