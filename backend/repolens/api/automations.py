"""Automations: turn on work that starts by itself (investigate a CI failure, review a new pull request),
see which model does it, and see what it did."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import AgentRule, AgentRun, Repository
from ..services import automations as svc
from ..services.policy import authorize

router = APIRouter(prefix="/api/automations", tags=["automations"])


class RuleIn(BaseModel):
    enabled: bool = False
    daily_limit: int = Field(default=5, ge=1, le=50)
    all_branches: bool = False
    event: str = Field(default=svc.CI, pattern="^(ci_failure|pr_review)$")


def _rule_view(r: AgentRule | None) -> dict:
    if r is None:
        return {"enabled": False, "daily_limit": 5, "all_branches": False}
    return {"enabled": r.enabled, "daily_limit": r.daily_limit, "all_branches": r.all_branches}


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
    return {"model": svc.model_info(db, p.org_id), "events": events, "runs": shown}


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
    rule.created_by = p.user_id
    rule.updated_at = datetime.now(UTC)
    db.commit()
    return _rule_view(rule)
