"""Automations: start an investigation by itself when a CI run fails.

An admin turns it on for a repository (or all of them). When GitHub tells RepoLens a workflow run failed, RepoLens reads
the log of each failed job, finds the failing tests or stack traces the same way a pasted log is read, and starts one
investigation per failure, at most `daily_limit` a day. Those investigations are marked automatic. They are the same
investigations a person starts: nothing is posted, commented or changed anywhere, and a person reads and decides.

The work runs as the admin who set the rule up, with that person's access. Every start is written to the audit log.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AgentRule, AgentRun, AuditEvent, Investigation, Repository, User
from ..security import redact_stored
from . import ci_logs
from .github import GitHubError
from .investigations import create_investigation, submit

log = logging.getLogger("repolens.automations")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="automation")

EVENT = "ci_failure"
MAX_JOBS = 3  # failed jobs read from one run
MAX_PER_RUN = 3  # investigations started from one run
TAIL = 6000  # characters of a log kept when no test or trace can be told apart in it


def norm(url: str) -> str:
    return url.lower().removesuffix(".git").replace("git@github.com:", "https://github.com/")


def rule_for(db: Session, org_id: str, repo_id: str) -> AgentRule | None:
    """The rule that applies to a repository: its own, else the one for every repository. Only a rule that is on."""
    rules = db.scalars(
        select(AgentRule).where(
            AgentRule.org_id == org_id, AgentRule.event == EVENT, AgentRule.repo_id.in_((repo_id, ""))
        )
    ).all()
    own = next((r for r in rules if r.repo_id == repo_id), None)
    chosen = own or next((r for r in rules if r.repo_id == ""), None)
    return chosen if chosen and chosen.enabled else None


def used_today(db: Session, org_id: str) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    return (
        db.scalar(
            select(func.count())
            .select_from(Investigation)
            .where(Investigation.org_id == org_id, Investigation.source == "ci_auto", Investigation.created_at >= since)
        )
        or 0
    )


def _skip(
    db: Session, org_id: str, rule: AgentRule, repo: Repository, key: str, why: str, title: str, link: str
) -> AgentRun:
    run = AgentRun(
        org_id=org_id,
        rule_id=rule.id,
        repo_id=repo.id,
        event=EVENT,
        event_key=key,
        status="skipped",
        reason=why,
        title=title,
        link=link,
    )
    db.add(run)
    db.commit()
    return run


def on_workflow_run(db: Session, payload: dict) -> dict:
    """The webhook calls this for a finished workflow run. It decides and records; the log is read in the background."""
    wr = payload.get("workflow_run") or {}
    if payload.get("action") != "completed" or wr.get("conclusion") not in ("failure", "timed_out"):
        return {"automation": "ignored"}
    clone_url = norm((payload.get("repository") or {}).get("clone_url", ""))
    full = (payload.get("repository") or {}).get("full_name") or ""
    run_id = wr.get("id")
    if not (clone_url and full and run_id):
        return {"automation": "ignored"}
    key = f"{run_id}-{wr.get('run_attempt') or 1}"
    title = f"{wr.get('name') or 'CI'} failed on {wr.get('head_branch') or 'a branch'}"[:300]
    link = str(wr.get("html_url") or "")[:500]
    started: list[str] = []
    for repo in db.scalars(select(Repository).where(Repository.source == "git")):
        if norm(repo.url) != clone_url:
            continue
        rule = rule_for(db, repo.org_id, repo.id)
        if rule is None:
            continue
        if db.scalar(
            select(AgentRun.id).where(
                AgentRun.org_id == repo.org_id, AgentRun.event == EVENT, AgentRun.event_key == key
            )
        ):
            continue  # GitHub sends an event again when delivery is retried
        branch = repo.branch or repo.default_branch
        if not rule.all_branches and wr.get("head_branch") != branch:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                key,
                f"Not the {branch} branch (this rule watches only that branch)",
                title,
                link,
            )
            continue
        left = rule.daily_limit - used_today(db, repo.org_id)
        if left <= 0:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                key,
                f"The limit of {rule.daily_limit} automatic investigations a day is used",
                title,
                link,
            )
            continue
        run = AgentRun(
            org_id=repo.org_id,
            rule_id=rule.id,
            repo_id=repo.id,
            event=EVENT,
            event_key=key,
            status="started",
            reason="Reading the failed jobs",
            title=title,
            link=link,
        )
        db.add(run)
        db.commit()
        if get_settings().run_jobs_inline:
            work(run.id, full, int(run_id), min(left, MAX_PER_RUN))
        else:
            _pool.submit(work, run.id, full, int(run_id), min(left, MAX_PER_RUN))
        started.append(run.id)
    return {"automation": started}


def work(agent_run_id: str, full_name: str, gh_run_id: int, room: int) -> None:
    """Read the failed jobs of one run and start the investigations. Never raises: the record says what happened."""
    from ..api.github import make_client  # imported here: the api package imports this one
    from ..db import session_factory

    with session_factory()() as db:
        run = db.get(AgentRun, agent_run_id)
        if run is None:
            return
        try:
            _work(db, run, full_name, gh_run_id, room, make_client)
        except GitHubError as e:
            db.rollback()
            _finish(db, run, "failed", f"GitHub said no: {e}")
        except Exception as e:  # the record is the only place anyone will see why nothing started
            log.exception("automation failed")
            db.rollback()
            _finish(db, run, "failed", f"Could not read the CI run ({type(e).__name__})")


def _finish(db: Session, run: AgentRun, status: str, reason: str, ids: list[str] | None = None) -> None:
    run = db.merge(run)
    run.status, run.reason = status, reason[:500]
    if ids is not None:
        run.investigation_ids = ids
    db.commit()


def _work(db: Session, run: AgentRun, full_name: str, gh_run_id: int, room: int, make_client) -> None:  # type: ignore[no-untyped-def]
    rule = db.get(AgentRule, run.rule_id)
    repo = db.get(Repository, run.repo_id)
    runner = db.get(User, rule.created_by) if rule and rule.created_by else None
    if rule is None or repo is None or runner is None or runner.role != "admin" or not runner.active:
        _finish(db, run, "skipped", "The admin who turned this on is no longer an active admin")
        return
    try:
        client = make_client(db, run.org_id)
    except Exception:
        _finish(db, run, "failed", "GitHub is not connected, so the log cannot be read")
        return
    jobs = client.failed_jobs(full_name, gh_run_id)[:MAX_JOBS]
    if not jobs:
        _finish(db, run, "skipped", "GitHub lists no failed job for this run")
        return
    found: list[ci_logs.Failure] = []
    tail = ""
    for job in jobs:
        text = client.job_log(full_name, int(job["id"]))
        tail = tail or text[-TAIL:]
        try:
            _, failures = ci_logs.parse_ci_log(text)
        except ci_logs.CiLogError:
            continue
        found += failures
    queries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for f in found:
        if (f.name, f.message) in seen:
            continue
        seen.add((f.name, f.message))
        f = ci_logs.sanitize(f)
        queries.append((ci_logs.to_query(f), f"CI failure: {f.name}"[:300]))
    if not queries and tail.strip():
        text, _ = redact_stored(tail)
        queries.append(
            (
                f"CI job failed: {jobs[0].get('name') or 'job'}\n\n{text}",
                f"CI failure: {jobs[0].get('name') or 'job'}"[:300],
            )
        )
    if not queries:
        _finish(db, run, "skipped", "The log has no failure RepoLens can tell apart")
        return
    ids: list[str] = []
    for query, title in queries[:room]:
        inv = create_investigation(db, run.org_id, rule.created_by, query, [repo.id], title)
        inv.source = "ci_auto"
        db.commit()
        ids.append(inv.id)
        submit(inv.id)
    db.add(
        AuditEvent(
            org_id=run.org_id,
            actor_id=rule.created_by,
            action="automation.ci_failure",
            target=repo.name[:200],
            allowed=True,
            detail={"run": run.link, "started": len(ids), "rule": rule.id},
        )
    )
    more = len(queries) - len(ids)
    _finish(
        db,
        run,
        "started",
        f"Started {len(ids)} investigation(s)"
        + (f"; {more} more not started (daily limit or the {MAX_PER_RUN} a run cap)" if more > 0 else ""),
        ids,
    )
