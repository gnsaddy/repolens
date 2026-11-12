"""More things that start the agent, and what it does for each.

* **drift_draft**: after an index, a page that describes code which changed (an open drift alert) gets a drafted update
  in the wiki's proposals, which a person approves as usual.
* **pr_comment for a failed CI run**: when the failed run belongs to a pull request, a draft comment waits in the Inbox
  until the investigation has finished, then holds its likely cause. An admin posts it, or not.
* **watch**: once a day, a plain list (no model) of pages that have been out of date for a long time, in the Inbox.

All of it follows the rules of the other automations: an admin turns each on, it runs as that admin, it respects the
pause and the daily token budget, it writes to the audit log, and nothing reaches GitHub without an approval.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgentDraft, AgentRun, AuditEvent, DriftAlert, Investigation, LLMCallEvent, Repository
from . import agent_controls
from .automations import DRIFT, WATCH, _finish, _seen, _skip, _submit, _who, rule_for, used_today

log = logging.getLogger("repolens.automations")
STALE_DAYS = 14  # a page that has been out of date this long is listed by the nightly watch
FRESH_DAYS = 3  # a drift alert older than this is not drafted for when an index finishes: it was seen already


# ------------------------------------------------------------------------------------------ a stale page gets a draft
def after_index(org_id: str) -> None:
    """Called when an index finishes. Does its work in the background; never raises into the index."""
    _submit(on_index, org_id)


def on_index(org_id: str) -> None:
    from ..db import session_factory

    with session_factory()() as db:
        since = datetime.now(UTC) - timedelta(days=FRESH_DAYS)
        for alert in db.scalars(
            select(DriftAlert).where(
                DriftAlert.org_id == org_id, DriftAlert.status == "open", DriftAlert.created_at >= since
            )
        ):
            try:
                _draft_for(db, alert)
            except Exception:  # one page must not stop the others, and never the index
                log.exception("could not draft an update for a stale page")
                db.rollback()


def _draft_for(db: Session, alert: DriftAlert) -> None:
    from . import proposals

    repo = db.get(Repository, alert.repo_id)
    if repo is None or _seen(db, alert.org_id, DRIFT, alert.id):
        return
    rule = rule_for(db, alert.org_id, alert.repo_id, DRIFT)
    if rule is None:
        return
    title = f"Stale page: {alert.doc_title or alert.doc_path}"[:300]
    blocked = agent_controls.gate(db, alert.org_id)
    if blocked:
        _skip(db, alert.org_id, rule, repo, DRIFT, alert.id, blocked, title, "")
        return
    if used_today(db, alert.org_id, DRIFT) >= rule.daily_limit:
        _skip(
            db,
            alert.org_id,
            rule,
            repo,
            DRIFT,
            alert.id,
            f"The limit of {rule.daily_limit} drafted pages a day is used",
            title,
            "",
        )
        return
    run = AgentRun(
        org_id=alert.org_id,
        rule_id=rule.id,
        repo_id=repo.id,
        event=DRIFT,
        event_key=alert.id,
        status="started",
        reason="Drafting the page",
        title=title,
    )
    agent_controls.trace(
        run,
        f"After an index, {alert.doc_path} describes code that changed ({len(alert.symbols)} symbol(s)); "
        f"the rule for this repository is on ({rule.daily_limit} a day).",
    )
    db.add(run)
    db.commit()
    who = _who(db, run)
    if who is None:
        return
    rule, _, runner = who
    try:
        proposal = proposals.draft_update(db, alert.org_id, rule.created_by, alert)
    except proposals.ProposalError as e:
        _finish(db, run, "failed", str(e))
        return
    if agent_controls.cancelled(db, run):
        return
    db.add(
        AgentDraft(
            org_id=alert.org_id,
            repo_id=repo.id,
            run_id=run.id,
            kind="wiki_proposal",
            title=title,
            body=f"A drafted update for `{alert.doc_path}` waits in the wiki proposals. "
            "Review and approve it there; nothing is written before you do.",
            meta={"proposal_id": proposal.id, "path": alert.doc_path},
        )
    )
    db.add(
        AuditEvent(
            org_id=alert.org_id,
            actor_id=runner.id,
            action="automation.drift_draft",
            target=repo.name[:200],
            allowed=True,
            detail={"page": alert.doc_path, "proposal": proposal.id},
        )
    )
    run.detail = {**(run.detail or {}), "workflow_ids": [alert.id]}
    _finish(db, run, "started", f"Drafted an update for {alert.doc_path}; it waits in the wiki proposals")


# ----------------------------------------------------------------------------------- a failed CI run on a pull request
def models_for(db: Session, workflow_id: str) -> list[str]:
    return sorted(
        {
            m
            for (m,) in db.execute(
                select(LLMCallEvent.model_name).where(
                    LLMCallEvent.workflow_id == workflow_id, LLMCallEvent.kind == "chat"
                )
            )
            if m
        }
    )


def compose_ci_comment(db: Session, inv: Investigation, link: str) -> str:
    """The likely cause of a failed CI run as a pull request comment. Only what the investigation concluded."""
    ai = bool((inv.evidence or {}).get("llm_used"))
    models = models_for(db, inv.id)
    out = ["### RepoLens looked at the failed CI run", ""]
    if link:
        out += [f"CI run: {link}", ""]
    out.append(
        f"**Likely cause** ({round((inv.confidence or 0) * 100)}% confidence): "
        f"{(inv.root_cause or 'not found').strip()}"
    )
    if inv.suggested_fix:
        out += ["", f"**Suggested fix:** {inv.suggested_fix.strip()}"]
    if inv.hypotheses:
        top = inv.hypotheses[0]
        out += ["", f"Strongest lead: {getattr(top, 'title', '')}"]
    out += [
        "",
        f"_{'AI generated by ' + ', '.join(models) if ai and models else 'Built from evidence by code'}. "
        "Check it against the code._",
    ]
    return "\n".join(out)


def on_investigation_done(db: Session, inv: Investigation) -> None:
    """An investigation an automation started has finished: fill in the comment waiting for it, if there is one."""
    if inv.source != "ci_auto":
        return
    runs = [
        r
        for r in db.scalars(
            select(AgentRun)
            .where(AgentRun.org_id == inv.org_id, AgentRun.event == "ci_failure")
            .order_by(AgentRun.created_at.desc())
            .limit(100)
        )
        if inv.id in (r.investigation_ids or [])
    ]
    for run in runs:
        for d in db.scalars(
            select(AgentDraft).where(
                AgentDraft.run_id == run.id, AgentDraft.kind == "pr_comment", AgentDraft.status.in_(("new", "read"))
            )
        ):
            if d.meta.get("investigation_id") != inv.id or not d.meta.get("pending"):
                continue
            if inv.status != "completed":
                d.status, d.error = "dismissed", "The investigation did not finish, so there is no comment"
            else:
                d.body = compose_ci_comment(db, inv, d.meta.get("link", ""))
                d.meta = {**d.meta, "pending": False}
    db.commit()


# ------------------------------------------------------------------------------------------------ the nightly watch
def nightly(db: Session, now: datetime | None = None) -> int:
    """Once a day: for every repository with the watch on, list pages that have been out of date for a long time. One
    list a week for each repository. No model is used. Returns how many lists were made."""
    now = now or datetime.now(UTC)
    week = now.strftime("%G-W%V")
    cutoff = now - timedelta(days=STALE_DAYS)
    made = 0
    for repo in db.scalars(select(Repository)):
        rule = rule_for(db, repo.org_id, repo.id, WATCH)
        key = f"{repo.id}-{week}"
        if rule is None or _seen(db, repo.org_id, WATCH, key):
            continue
        stale = db.scalars(
            select(DriftAlert)
            .where(DriftAlert.repo_id == repo.id, DriftAlert.status == "open", DriftAlert.created_at < cutoff)
            .order_by(DriftAlert.created_at)
        ).all()
        if not stale:
            continue
        title = f"{repo.name}: {len(stale)} page(s) out of date for over {STALE_DAYS} days"
        blocked = agent_controls.gate(db, repo.org_id)
        if blocked:
            _skip(db, repo.org_id, rule, repo, WATCH, key, blocked, title, "")
            continue
        body = "\n".join(
            [f"### Pages that describe code that changed more than {STALE_DAYS} days ago", ""]
            + [
                f"- `{a.doc_path}` since {a.created_at.date().isoformat()}: "
                + ", ".join(f"`{s.get('qualname') or s.get('name')}`" for s in a.symbols[:4])
                for a in stale[:20]
            ]
        )
        run = AgentRun(
            org_id=repo.org_id,
            rule_id=rule.id,
            repo_id=repo.id,
            event=WATCH,
            event_key=key,
            status="started",
            reason="Listed the pages",
            title=title,
        )
        agent_controls.trace(
            run,
            f"The nightly watch found {len(stale)} open drift alert(s) older than {STALE_DAYS} days in {repo.name}.",
        )
        db.add(run)
        db.commit()
        db.add(
            AgentDraft(
                org_id=repo.org_id,
                repo_id=repo.id,
                run_id=run.id,
                kind="watch",
                title=title,
                body=body,
                meta={"pages": len(stale)},
            )
        )
        db.add(
            AuditEvent(
                org_id=repo.org_id,
                actor_id=rule.created_by,
                action="automation.watch",
                target=repo.name[:200],
                allowed=True,
                detail={"pages": len(stale)},
            )
        )
        db.commit()
        made += 1
    return made
