"""Controls over the agent: a pause switch, a daily token budget, cancelling a run, the trace of why a run happened,
retries when GitHub cannot be reached, and the agent's health.

Everything here is about the agent's own runs (`agent_runs`). It reads what the model calls cost from the call log the
model client keeps, so the budget counts what really happened.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AgentDraft, AgentRule, AgentRun, AgentSetting, AuditEvent, LLMCallEvent
from .github import GitHubError

STUCK_AFTER = timedelta(minutes=45)  # a run that has said "started" this long was cut short (a restart, a hung call)
TRACE_MAX = 40


class TransientError(Exception):
    """A failure that can pass by itself and is worth trying again after a wait: a model provider that is busy."""


_BUSY = re.compile(r"\b(429|500|502|503|504)\b|high demand|overloaded|rate limit|quota|timed out|unavailable", re.I)


def busy(message: str) -> bool:
    """Whether a model's error says the provider is busy or limiting requests, not that the request was wrong."""
    return bool(_BUSY.search(message))


def setting(db: Session, org_id: str) -> AgentSetting | None:
    return db.scalar(select(AgentSetting).where(AgentSetting.org_id == org_id))


def is_paused(db: Session, org_id: str) -> bool:
    row = setting(db, org_id)
    return bool(row and row.paused)


def token_budget(db: Session, org_id: str) -> int:
    row = setting(db, org_id)
    return int(row.token_budget) if row else 0


def working(run: AgentRun) -> bool:
    """Whether the run's work is still going on. A finished run keeps the status `started` (it did start things), so the
    status alone cannot say; the run is marked from its start until `_finish`."""
    return bool((run.detail or {}).get("working"))


def mark_working(run: AgentRun) -> None:
    run.detail = {**(run.detail or {}), "working": True}


def workflow_ids(run: AgentRun) -> list[str]:
    """Ids the model calls of this run were logged under: investigations, persona reviews, a wiki draft."""
    d = run.detail or {}
    return [
        *(run.investigation_ids or []),
        *(r["id"] for r in d.get("reviews", []) if r.get("id")),
        *d.get("workflow_ids", []),
    ]


def tokens_used(db: Session, org_id: str, hours: int = 24) -> int:
    """Tokens the agent's runs of the last day used, input and output."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    ids = [
        i
        for run in db.scalars(select(AgentRun).where(AgentRun.org_id == org_id, AgentRun.created_at >= since))
        for i in workflow_ids(run)
    ]
    if not ids:
        return 0
    return int(
        db.scalar(
            select(func.coalesce(func.sum(LLMCallEvent.input_tokens + LLMCallEvent.output_tokens), 0)).where(
                LLMCallEvent.org_id == org_id, LLMCallEvent.workflow_id.in_(ids), LLMCallEvent.kind == "chat"
            )
        )
        or 0
    )


def gate(db: Session, org_id: str) -> str | None:
    """Why nothing should start by itself now, or None. Checked before a run is made; a person can still run one."""
    if is_paused(db, org_id):
        return "The agent is paused by an admin"
    budget = token_budget(db, org_id)
    if budget:
        used = tokens_used(db, org_id)
        if used >= budget:
            return f"The daily token budget of {budget:,} is used ({used:,} tokens in the last day)"
    return None


def trace(run: AgentRun, what: str) -> None:
    """Add one line to the run's account of why it happened and what it did (shown on the Agent page)."""
    d = dict(run.detail or {})
    lines = list(d.get("trace", []))
    if len(lines) < TRACE_MAX:
        lines.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "what": what[:300]})
    d["trace"] = lines
    run.detail = d


def cancelled(db: Session, run: AgentRun) -> bool:
    """Whether an admin cancelled this run while its work was going on. Workers ask between their steps."""
    db.commit()  # what the worker noted so far is kept; refreshing would drop it
    db.refresh(run)
    return run.status == "cancelled"


def cancel(db: Session, run: AgentRun, user_id: str) -> AgentRun:
    """Stop a run that is going on: the work ends at its next step and what it drafted steps aside. A model call
    that is already in flight is not interrupted, and an investigation already handed over finishes by itself."""
    if run.status != "started" or not working(run):
        raise ValueError("Only a run that is going on can be cancelled")
    run.status, run.reason = "cancelled", "Cancelled by an admin"
    trace(run, "Cancelled by an admin")
    for d in db.scalars(select(AgentDraft).where(AgentDraft.run_id == run.id, AgentDraft.status.in_(("new", "read")))):
        db.delete(d)  # what a cancelled run drafted is not kept
    db.add(
        AuditEvent(
            org_id=run.org_id,
            actor_id=user_id,
            action="automation.cancel",
            target=run.event,
            allowed=True,
            detail={"link": run.link},
        )
    )
    db.commit()
    return run


def transient(e: GitHubError) -> bool:
    """A failure that can pass by itself: GitHub unreachable, a rate limit or a server error. A refusal is not."""
    if e.status is None:
        return True
    return e.status == 429 or e.status >= 500 or "rate limit" in str(e).lower()


def next_delay(run: AgentRun, delays: list[float]) -> float | None:
    """How long to wait before the next automatic try, or None when the tries are used up."""
    tried = int((run.detail or {}).get("attempts", 1))
    return delays[tried - 1] if tried - 1 < len(delays) else None


def reap_stuck(db: Session, org_id: str | None = None) -> int:
    """Mark runs that can no longer finish as failed, so the page does not show them as going on for ever."""
    cutoff = datetime.now(UTC) - STUCK_AFTER
    q = select(AgentRun).where(AgentRun.status == "started", AgentRun.created_at < cutoff)
    if org_id:
        q = q.where(AgentRun.org_id == org_id)
    n = 0
    for run in db.scalars(q):
        if not working(run):
            continue  # it finished; `started` is also what a finished run says
        run.status, run.reason = "failed", "It did not finish (the server was restarted or a call hung). Try again."
        trace(run, "Marked as failed: it had been going on for over 45 minutes")
        n += 1
    if n:
        db.commit()
    return n


def health(db: Session, org_id: str) -> dict:
    """What an admin should know about the agent: whether it is paused, what it has used, what failed lately."""
    since = datetime.now(UTC) - timedelta(hours=24)
    failed = (
        db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.org_id == org_id, AgentRun.status == "failed", AgentRun.created_at >= since)
        )
        or 0
    )
    old = db.scalars(
        select(AgentRun).where(
            AgentRun.org_id == org_id,
            AgentRun.status == "started",
            AgentRun.created_at < datetime.now(UTC) - STUCK_AFTER,
        )
    )
    stuck = sum(1 for r in old if working(r))
    budget, used = token_budget(db, org_id), tokens_used(db, org_id)
    problems = []
    if failed:
        problems.append(f"{failed} automatic run{'s' if failed != 1 else ''} failed in the last day")
    if stuck:
        problems.append(f"{stuck} automatic run{'s' if stuck != 1 else ''} has been going on for over 45 minutes")
    if budget and used >= budget:
        problems.append("The agent's daily token budget is used, so nothing starts by itself")
    return {
        "paused": is_paused(db, org_id),
        "token_budget": budget,
        "tokens_used": used,
        "failed_24h": failed,
        "stuck": stuck,
        "problems": problems,
    }


def summary(db: Session, org_id: str) -> dict:
    """The agent at a glance, for the Dashboard and Insights: is it on, what it did in the last day, what waits."""
    since = datetime.now(UTC) - timedelta(hours=24)
    runs = db.scalars(select(AgentRun).where(AgentRun.org_id == org_id, AgentRun.created_at >= since)).all()
    by_event = {e: {"runs": 0, "failed": 0} for e in ("ci_failure", "pr_review", "drift_draft", "watch")}
    for r in runs:
        row = by_event.setdefault(r.event, {"runs": 0, "failed": 0})
        row["runs"] += 1
        row["failed"] += 1 if r.status == "failed" else 0
    going = db.scalars(select(AgentRun).where(AgentRun.org_id == org_id, AgentRun.status == "started")).all()
    last = db.scalar(select(AgentRun).where(AgentRun.org_id == org_id).order_by(AgentRun.created_at.desc()).limit(1))
    return {
        "paused": is_paused(db, org_id),
        "rules_on": db.scalar(
            select(func.count()).select_from(AgentRule).where(AgentRule.org_id == org_id, AgentRule.enabled.is_(True))
        )
        or 0,
        "runs_24h": len(runs),
        "failed_24h": sum(1 for r in runs if r.status == "failed"),
        "working_now": sum(1 for r in going if working(r)),
        "waiting": db.scalar(
            select(func.count())
            .select_from(AgentDraft)
            .where(AgentDraft.org_id == org_id, AgentDraft.status.in_(("new", "read")))
        )
        or 0,
        "tokens_used": tokens_used(db, org_id),
        "token_budget": token_budget(db, org_id),
        "by_event": by_event,
        "last_run": {"title": last.title, "event": last.event, "status": last.status, "created_at": last.created_at}
        if last
        else None,
    }
