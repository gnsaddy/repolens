"""Automations: start work by itself when something happens on GitHub.

Two events today, each switched on by an admin per repository (or for all of them), each off until then:

* **ci_failure**: a workflow run failed. RepoLens reads the log of each failed job, finds the failing tests or stack
  traces the way a pasted log is read, and starts one investigation per failure, at most `daily_limit` a day.
* **pr_review**: a pull request was opened (or reopened, or taken out of draft). RepoLens reads the changed files as the
  pull request has them and has two specialists (the developer and the security auditor) review them, at most
  `daily_limit` pull requests a day. A review needs a language model; without one the run says so and makes nothing up.

What they start are the same investigations and reviews a person can start: nothing is posted, commented or changed on
GitHub, and a person reads and decides. The work runs as the admin who set the rule up, with that person's access, and
every start is written to the audit log. Which model wrote what is kept on the record.
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AgentDraft,
    AgentRule,
    AgentRun,
    AuditEvent,
    Investigation,
    LLMCallEvent,
    PersonaRun,
    Repository,
    User,
)
from ..security import redact_stored
from . import agent_controls, ci_logs
from .access import scope_for_user_id
from .github import GitHubError
from .investigations import create_investigation, submit

log = logging.getLogger("repolens.automations")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="automation")

CI = "ci_failure"
PR = "pr_review"
DRIFT = "drift_draft"
WATCH = "watch"
EVENTS = (CI, PR, DRIFT, WATCH)
EVENT = CI  # kept for callers that mean the first event
MAX_JOBS = 3  # failed jobs read from one run
MAX_PER_RUN = 3  # investigations started from one run
TAIL = 6000  # characters of a log kept when no test or trace can be told apart in it
PR_PERSONAS = ("developer", "security-auditor")
MAX_PR_FILES = 10  # changed files read for one review
MAX_FILE_CHARS = 120_000
MAX_REVIEWS_PER_PR = 5  # a pull request reviewed again after new commits is reviewed at most this often
PR_ACTIONS = ("opened", "reopened", "ready_for_review")


def norm(url: str) -> str:
    return url.lower().removesuffix(".git").replace("git@github.com:", "https://github.com/")


def rule_for(db: Session, org_id: str, repo_id: str, event: str = CI) -> AgentRule | None:
    """The rule that applies to a repository: its own, else the one for every repository. Only a rule that is on."""
    rules = db.scalars(
        select(AgentRule).where(
            AgentRule.org_id == org_id, AgentRule.event == event, AgentRule.repo_id.in_((repo_id, ""))
        )
    ).all()
    own = next((r for r in rules if r.repo_id == repo_id), None)
    chosen = own or next((r for r in rules if r.repo_id == ""), None)
    return chosen if chosen and chosen.enabled else None


def used_today(db: Session, org_id: str, event: str = CI) -> int:
    """What the daily limit counts: investigations started (CI failures) or pull requests reviewed, in the last day."""
    since = datetime.now(UTC) - timedelta(hours=24)
    if event != CI:
        return (
            db.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.org_id == org_id,
                    AgentRun.event == event,
                    AgentRun.status == "started",
                    AgentRun.created_at >= since,
                )
            )
            or 0
        )
    return (
        db.scalar(
            select(func.count())
            .select_from(Investigation)
            .where(Investigation.org_id == org_id, Investigation.source == "ci_auto", Investigation.created_at >= since)
        )
        or 0
    )


def model_info(db: Session, org_id: str) -> dict:
    """Which model the agent would use now, and whether there is one. Without one nothing is AI written. `routing` says
    which tier and model each of the agent's tasks starts on (the routing plan of Settings, AI and
    search); a harder task
    can move up a tier and a failing model hands over to the next, so the runs list shows the model that really ran."""
    from . import llm_router
    from .llm import LLMClient

    llm = LLMClient(db, org_id, workflow="automation", workflow_id="")
    routes = llm_router.all_routes(db, org_id)
    savings = llm_router.savings_mode(db, org_id)
    # the same tasks the code runs: a review asks for the strong tier (see personas.run), a report its own tier
    tasks = (
        ("Investigation reports", llm_router.Task("investigator")),
        ("Pull request reviews", llm_router.Task("persona-reviewer", prefer="strong")),
    )
    routing = []
    for name, task in tasks:
        d = llm_router.decide(routes, task, savings)
        first = d.chain[0] if d.chain else None
        routing.append(
            {
                "task": name,
                "tier": d.tier,
                "provider": first.cfg.provider if first else None,
                "model": first.cfg.model if first else None,
                "why": d.why[0] if d.why else "",
            }
        )
    return {
        "ai": not llm.offline,
        "provider": llm.provider,
        "model": llm.model,
        "multi": len(routes) > 1,
        "routing": routing,
    }


_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_FAILURE_WORDS = re.compile(r"\b(error|errors|failed|failure|fatal|exception|traceback|vulnerabilit\w*)\b", re.I)


def failure_excerpt(text: str, cap: int = TAIL) -> str:
    """The part of a job log that says what went wrong. GitHub marks real errors with `##[error]`, and the steps after
    the failure (uploading artifacts, cleaning up) fill the end of the log, so the tail is a poor place to look: take
    the lines before the first error mark, then the error marks themselves. A log without marks is searched for the
    first line that talks about a failure; only then is its end used."""
    lines = [_STAMP.sub("", ln.rstrip()) for ln in text.splitlines()]
    marks = [i for i, ln in enumerate(lines) if "##[error]" in ln]
    if marks:
        start = max(0, marks[0] - 60)
        body = lines[start : marks[0]] + [lines[i] for i in marks[:20]]
    else:
        first = next((i for i, ln in enumerate(lines) if _FAILURE_WORDS.search(ln)), None)
        body = lines[max(0, first - 20) : first + 40] if first is not None else lines[-60:]
    out = "\n".join(body)
    return out[-cap:] if len(out) > cap else out


def _skip(
    db: Session, org_id: str, rule: AgentRule, repo: Repository, event: str, key: str, why: str, title: str, link: str
) -> AgentRun:
    run = AgentRun(
        org_id=org_id,
        rule_id=rule.id,
        repo_id=repo.id,
        event=event,
        event_key=key,
        status="skipped",
        reason=why,
        title=title,
        link=link,
    )
    db.add(run)
    db.commit()
    return run


def _seen(db: Session, org_id: str, event: str, key: str) -> bool:
    return bool(
        db.scalar(
            select(AgentRun.id).where(AgentRun.org_id == org_id, AgentRun.event == event, AgentRun.event_key == key)
        )
    )


def _repos(db: Session, payload: dict) -> list[Repository]:
    clone_url = norm((payload.get("repository") or {}).get("clone_url", ""))
    return [
        r
        for r in db.scalars(select(Repository).where(Repository.source == "git"))
        if clone_url and norm(r.url) == clone_url
    ]


def _submit(fn, *args) -> None:  # type: ignore[no-untyped-def]
    if get_settings().run_jobs_inline:
        fn(*args)
    else:
        _pool.submit(fn, *args)


# ------------------------------------------------------------------------------------------------ CI failures
def on_workflow_run(db: Session, payload: dict) -> dict:
    """The webhook calls this for a finished workflow run. It decides and records; the log is read in the background."""
    wr = payload.get("workflow_run") or {}
    if payload.get("action") != "completed" or wr.get("conclusion") not in ("failure", "timed_out"):
        return {"automation": "ignored"}
    full = (payload.get("repository") or {}).get("full_name") or ""
    run_id = wr.get("id")
    if not full or not run_id:
        return {"automation": "ignored"}
    key = f"{run_id}-{wr.get('run_attempt') or 1}"
    title = f"{wr.get('name') or 'CI'} failed on {wr.get('head_branch') or 'a branch'}"[:300]
    link = str(wr.get("html_url") or "")[:500]
    started: list[str] = []
    for repo in _repos(db, payload):
        rule = rule_for(db, repo.org_id, repo.id, CI)
        if rule is None or _seen(db, repo.org_id, CI, key):  # GitHub sends an event again when delivery is retried
            continue
        branch = repo.branch or repo.default_branch
        blocked = agent_controls.gate(db, repo.org_id)
        if blocked:
            _skip(db, repo.org_id, rule, repo, CI, key, blocked, title, link)
            continue
        if not rule.all_branches and wr.get("head_branch") != branch:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                CI,
                key,
                f"Not the {branch} branch (this rule watches only that branch)",
                title,
                link,
            )
            continue
        left = rule.daily_limit - used_today(db, repo.org_id, CI)
        if left <= 0:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                CI,
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
            event=CI,
            event_key=key,
            status="started",
            reason="Reading the failed jobs",
            title=title,
            link=link,
        )
        prs = [int(x["number"]) for x in (wr.get("pull_requests") or []) if isinstance(x, dict) and x.get("number")][:3]
        if prs:
            run.detail = {"pull_requests": prs, "head": str(wr.get("head_sha") or "")[:40]}
        agent_controls.mark_working(run)
        agent_controls.trace(
            run,
            f"GitHub reported that workflow run {run_id} failed on {wr.get('head_branch') or 'a branch'}. "
            f"The rule for {'this repository' if rule.repo_id else 'every repository'} is on ({rule.daily_limit} a day"
            f"{', every branch' if rule.all_branches else f', branch {branch}'}); {left} investigation(s) left today.",
        )
        db.add(run)
        db.commit()
        _submit(work, run.id, full, int(run_id), min(left, MAX_PER_RUN))
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
            if _again(db, run, e, work, (agent_run_id, full_name, gh_run_id, room)):
                return
            _finish(db, run, "failed", f"GitHub said no: {e}")
        except Exception as e:  # the record is the only place anyone will see why nothing started
            log.exception("automation failed")
            db.rollback()
            _finish(db, run, "failed", f"Could not read the CI run ({type(e).__name__})")


def _finish(
    db: Session, run: AgentRun, status: str, reason: str, ids: list[str] | None = None, detail: dict | None = None
) -> None:
    if run.status == "cancelled":
        return  # an admin cancelled it: that stands
    keep = {k: v for k, v in (run.detail or {}).items() if k in ("trace", "attempts", "pull_requests", "workflow_ids")}
    run.status, run.reason = status, reason[:500]
    if ids is not None:
        run.investigation_ids = ids
    base = dict(detail) if detail is not None else dict(run.detail or {})
    base.pop("working", None)  # finished
    run.detail = {**base, **keep}
    agent_controls.trace(run, f"{status}: {reason}")
    db.commit()


def _again(db: Session, run: AgentRun, e: Exception, fn, args: tuple) -> bool:  # type: ignore[no-untyped-def]
    """GitHub could not be reached, or was rate limiting: try the same work again after a wait, a few times."""
    if not (
        isinstance(e, agent_controls.TransientError) or (isinstance(e, GitHubError) and agent_controls.transient(e))
    ):
        return False
    delays = get_settings().automation_retry_delays
    wait = agent_controls.next_delay(run, delays)
    if wait is None:
        return False
    tried = int((run.detail or {}).get("attempts", 1))
    run.detail = {**(run.detail or {}), "attempts": tried + 1, "working": True}
    run.status = "started"
    run.reason = (
        f"{'The AI model was busy' if isinstance(e, agent_controls.TransientError) else 'GitHub could not be reached'} "
        f"({e}); trying again in {wait / 60:g} minute(s), "
        f"try {tried + 1} of {len(delays) + 1}"
    )
    agent_controls.trace(run, run.reason)
    db.commit()
    if get_settings().run_jobs_inline or wait <= 0:
        fn(*args)
    else:
        timer = threading.Timer(wait, lambda: _pool.submit(fn, *args))
        timer.daemon = True  # a wait must not keep the server from stopping
        timer.start()
    return True


def _who(db: Session, run: AgentRun) -> tuple[AgentRule, Repository, User] | None:
    """The rule, the repository and the admin the work runs as, or None (and the run says why) when one is gone."""
    rule = db.get(AgentRule, run.rule_id)
    repo = db.get(Repository, run.repo_id)
    runner = db.get(User, rule.created_by) if rule and rule.created_by else None
    if rule is None or repo is None or runner is None or runner.role != "admin" or not runner.active:
        _finish(db, run, "skipped", "The admin who turned this on is no longer an active admin")
        return None
    return rule, repo, runner


def _work(db: Session, run: AgentRun, full_name: str, gh_run_id: int, room: int, make_client) -> None:  # type: ignore[no-untyped-def]
    who = _who(db, run)
    if who is None:
        return
    rule, repo, _ = who
    try:
        client = make_client(db, run.org_id)
    except Exception:
        _finish(db, run, "failed", "GitHub is not connected, so the log cannot be read")
        return
    jobs = client.failed_jobs(full_name, gh_run_id)[:MAX_JOBS]
    agent_controls.trace(run, f"GitHub lists {len(jobs)} failed job(s); their logs are read from the end.")
    if not jobs:
        _finish(db, run, "skipped", "GitHub lists no failed job for this run")
        return
    found: list[ci_logs.Failure] = []
    first_log = ""
    for job in jobs:
        text = client.job_log(full_name, int(job["id"]))
        first_log = first_log or text
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
    if not queries and first_log.strip():
        text, _ = redact_stored(failure_excerpt(first_log))
        name = jobs[0].get("name") or "job"
        queries.append((f"CI job failed: {name}\n\n{text}", f"CI failure: {name}"[:300]))
    if not queries:
        _finish(db, run, "skipped", "The log has no failure RepoLens can tell apart")
        return
    ids: list[str] = []
    for query, title in queries[:room]:
        if agent_controls.cancelled(db, run):
            return
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
    for number in (run.detail or {}).get("pull_requests", []):
        db.add(
            AgentDraft(
                org_id=run.org_id,
                repo_id=repo.id,
                run_id=run.id,
                kind="pr_comment",
                title=f"CI failure on pull request #{number}",
                body="",
                meta={
                    "repo": full_name,
                    "number": number,
                    "head": (run.detail or {}).get("head", ""),
                    "link": run.link,
                    "pending": True,
                    "investigation_id": ids[0],
                },
            )
        )
    draft_investigations(db, run, repo, ids)
    more = len(queries) - len(ids)
    note = f"; {more} more not started (daily limit or the {MAX_PER_RUN} a run cap)" if more > 0 else ""
    _finish(db, run, "started", f"Started {len(ids)} investigation(s)" + note, ids)


# ------------------------------------------------------------------------------------------------ pull request review
def on_pull_request(db: Session, payload: dict) -> dict:
    """A pull request was opened, reopened or taken out of draft: decide, record, and review it in the background."""
    pr = payload.get("pull_request") or {}
    full = (payload.get("repository") or {}).get("full_name") or ""
    number = pr.get("number")
    action = payload.get("action")
    if action not in (*PR_ACTIONS, "synchronize") or not number or not full:
        return {"review": "ignored"}
    if pr.get("draft"):
        return {"review": "ignored"}  # reviewed when it is taken out of draft
    key = f"{number}-{str((pr.get('head') or {}).get('sha') or '')[:12]}"
    title = f"PR #{number}: {pr.get('title') or ''}"[:300]
    link = str(pr.get("html_url") or "")[:500]
    base = str((pr.get("base") or {}).get("ref") or "")
    started: list[str] = []
    for repo in _repos(db, payload):
        rule = rule_for(db, repo.org_id, repo.id, PR)
        if rule is None or _seen(db, repo.org_id, PR, key):
            continue
        if action == "synchronize" and not rule.re_review:
            continue  # a new commit is only reviewed when the rule says so
        if reviews_of(db, repo.org_id, number) >= MAX_REVIEWS_PER_PR:
            _skip(db, repo.org_id, rule, repo, PR, key, f"Already reviewed {MAX_REVIEWS_PER_PR} times", title, link)
            continue
        branch = repo.branch or repo.default_branch
        blocked = agent_controls.gate(db, repo.org_id)
        if blocked:
            _skip(db, repo.org_id, rule, repo, PR, key, blocked, title, link)
            continue
        if not rule.all_branches and base != branch:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                PR,
                key,
                f"Not a pull request into {branch} (this rule watches only that branch)",
                title,
                link,
            )
            continue
        if used_today(db, repo.org_id, PR) >= rule.daily_limit:
            _skip(
                db,
                repo.org_id,
                rule,
                repo,
                PR,
                key,
                f"The limit of {rule.daily_limit} reviewed pull requests a day is used",
                title,
                link,
            )
            continue
        run = AgentRun(
            org_id=repo.org_id,
            rule_id=rule.id,
            repo_id=repo.id,
            event=PR,
            event_key=key,
            status="started",
            reason="Reading the changed files",
            title=title,
            link=link,
        )
        agent_controls.mark_working(run)
        agent_controls.trace(
            run,
            f"GitHub reported that pull request #{number} was {action}. The rule for "
            f"{'this repository' if rule.repo_id else 'every repository'} is on ({rule.daily_limit} a day"
            f"{', every branch' if rule.all_branches else f', pull requests into {branch}'}).",
        )
        db.add(run)
        db.commit()
        _submit(work_pr, run.id, full, int(number))
        started.append(run.id)
    return {"review": started}


_SECRET = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.(pem|key|crt|p12|pfx)|id_(rsa|ed25519|ecdsa)[^/]*)$", re.I)
_LOCKFILE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|pipfile\.lock|go\.sum|cargo\.lock|gemfile\.lock|composer\.lock)$",
    re.I,
)


def skip_reason(path: str) -> str | None:
    """Why a changed file is not sent to the model for a review, or None. Only files that may hold a secret and lock
    files (long and machine written) are left out: a Dockerfile, a CI workflow or a manifest is what a reviewer should
    read. (The list a suggested fix must never edit is a different list, and was wrongly used here before.)"""
    if _SECRET.search(path):
        return "it may hold a secret"
    if _LOCKFILE.search(path):
        return "it is a lock file"
    return None


def hunks(patch: str) -> list[tuple[int, int]]:
    """The line ranges a unified diff changed, in the new file."""
    out = []
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch or "", re.M):
        start, n = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        if n:
            out.append((start, start + n - 1))
    return out


def work_pr(agent_run_id: str, full_name: str, number: int) -> None:
    """Read a pull request's changed files and have the specialists review them. Never raises."""
    from ..api.github import make_client
    from ..db import session_factory

    with session_factory()() as db:
        run = db.get(AgentRun, agent_run_id)
        if run is None:
            return
        try:
            _work_pr(db, run, full_name, number, make_client)
        except (GitHubError, agent_controls.TransientError) as e:
            db.rollback()
            if _again(db, run, e, work_pr, (agent_run_id, full_name, number)):
                return
            _finish(db, run, "failed", f"GitHub said no: {e}")
        except Exception as e:
            log.exception("pull request review failed")
            db.rollback()
            _finish(db, run, "failed", f"Could not review the pull request ({type(e).__name__})")


def _work_pr(db: Session, run: AgentRun, full_name: str, number: int, make_client) -> None:  # type: ignore[no-untyped-def]
    from . import personas

    who = _who(db, run)
    if who is None:
        return
    rule, repo, runner = who
    if not model_info(db, run.org_id)["ai"]:
        _finish(
            db,
            run,
            "skipped",
            "No language model is connected, so there is no AI review. Add a provider under Settings.",
        )
        return
    try:
        client = make_client(db, run.org_id)
    except Exception:
        _finish(db, run, "failed", "GitHub is not connected, so the pull request cannot be read")
        return
    pull = client.get_pull(full_name, number)
    head = str((pull.get("head") or {}).get("sha") or "")
    title = str(pull.get("title") or "")
    files = [f for f in client.pull_files(full_name, number) if f.get("status") != "removed" and f.get("filename")]
    skipped: list[dict] = [
        {"file": str(f["filename"]), "why": w} for f in files if (w := skip_reason(str(f["filename"])))
    ]
    files = [f for f in files if not skip_reason(str(f["filename"]))]
    files.sort(key=lambda f: -(int(f.get("additions") or 0) + int(f.get("deletions") or 0)))
    texts: dict[str, str] = {}
    spans: dict[str, list[tuple[int, int]]] = {}
    for f in files:
        path = str(f["filename"])
        if len(texts) >= MAX_PR_FILES:
            skipped.append({"file": path, "why": f"only the {MAX_PR_FILES} largest changes are read"})
            continue
        text = client.file_text(full_name, path, head, MAX_FILE_CHARS)
        if text is None:
            skipped.append({"file": path, "why": "it is binary, too large or could not be read"})
            continue
        texts[path] = text
        spans[path] = hunks(str(f.get("patch") or "")) or [(1, 1)]
    agent_controls.trace(
        run,
        f"Read {len(texts)} changed file(s) at {head[:7]} (at most {MAX_PR_FILES}; binary, removed and "
        "files that may hold a secret and lock files are skipped).",
    )
    if skipped:
        agent_controls.trace(run, "Not read: " + "; ".join(f"{s['file']} ({s['why']})" for s in skipped[:10]))
    if not texts:
        _finish(db, run, "skipped", "The pull request changes no text file that can be read")
        return
    request = (
        f"Review pull request #{number}: {title}\n\nChanged files: {', '.join(texts)}\n"
        f"Read at the pull request's latest commit {head[:7]}; the lines it changed are the ones to look at."
    )
    if skipped:
        request += (
            "\n\nChanged but NOT shown to you: "
            + "; ".join(f"{s['file'][:80]} ({s['why']})" for s in skipped[:6])
            + ". Do not say anything about what these contain, and do not say the pull request "
            "changes only what you were shown."
        )
    request = request[: personas.REQUEST_MAX]
    scope = scope_for_user_id(db, rule.created_by)
    reviews: list[dict] = []
    for key in PR_PERSONAS:
        if agent_controls.cancelled(db, run):
            return
        for attempt in (1, 2):  # a model that answers with something unusable is tried once more
            try:
                row, _ = personas.run(
                    db, run.org_id, rule.created_by, scope, repo, key, request, fresh=True, material=(texts, spans)
                )
                reviews.append(
                    {
                        "id": row.id,
                        "persona": key,
                        "status": row.status,
                        "model": row.model,
                        "findings": len(row.findings or []),
                        "summary": (row.summary or "")[:300],
                        "attempts": attempt,
                    }
                )
                break
            except personas.PersonaError as e:
                if attempt == 1 and "could not write the review" in str(e):
                    continue
                reviews.append(
                    {
                        "id": "",
                        "persona": key,
                        "status": "failed",
                        "model": "",
                        "findings": 0,
                        "summary": str(e)[:300],
                        "attempts": attempt,
                    }
                )
                break
    done = [r for r in reviews if r["status"] == "done"]
    if not done and all(agent_controls.busy(r["summary"]) for r in reviews):
        if agent_controls.next_delay(run, get_settings().automation_retry_delays) is not None:
            raise agent_controls.TransientError(
                reviews[0]["summary"][:160]
            )  # the model is busy: try the whole review later
    db.add(
        AuditEvent(
            org_id=run.org_id,
            actor_id=runner.id,
            action="automation.pr_review",
            target=repo.name[:200],
            allowed=True,
            detail={"pull_request": number, "link": run.link, "reviews": len(done), "rule": rule.id},
        )
    )
    detail = {
        "kind": PR,
        "pull_request": number,
        "head": head[:12],
        "files": list(texts),
        "skipped": skipped[:20],
        "reviews": reviews,
    }
    if not done:
        _finish(
            db,
            run,
            "failed",
            "The reviews could not be written: " + "; ".join(r["summary"] for r in reviews)[:300],
            detail=detail,
        )
        return
    if agent_controls.cancelled(db, run):
        return
    draft_review(db, run, repo, full_name, number, head, [r["id"] for r in done])
    total = sum(r["findings"] for r in done)
    _finish(db, run, "started", f"{len(done)} review(s) of {len(texts)} file(s): {total} finding(s)", detail=detail)


# ---------------------------------------------------------------------------------------------- what ran, and with what
def describe_runs(db: Session, runs: list[AgentRun]) -> list[dict]:
    """Each run with the model facts: was a model used, which one, and how many tokens. Read from the call log the
    model client keeps, so it is what happened rather than what was configured."""
    inv_ids = [i for r in runs for i in (r.investigation_ids or [])]
    inv: dict[str, dict] = {}
    if inv_ids:
        for i in db.scalars(select(Investigation).where(Investigation.id.in_(inv_ids))):
            inv[i.id] = {
                "id": i.id,
                "status": i.status,
                "ai": bool((i.evidence or {}).get("llm_used")),
                "models": [],
                "tokens": 0,
                "title": i.title,
            }
        for wf, model, tin, tout in db.execute(
            select(
                LLMCallEvent.workflow_id, LLMCallEvent.model_name, LLMCallEvent.input_tokens, LLMCallEvent.output_tokens
            ).where(LLMCallEvent.workflow_id.in_(inv_ids), LLMCallEvent.kind == "chat")
        ):
            d = inv.get(wf)
            if d is not None:
                if model and model not in d["models"]:
                    d["models"].append(model)
                d["tokens"] += int(tin or 0) + int(tout or 0)
    wfs = {r.id: agent_controls.workflow_ids(r) for r in runs}
    per_wf: dict[str, int] = {}
    if any(wfs.values()):
        for w, tin, tout in db.execute(
            select(LLMCallEvent.workflow_id, LLMCallEvent.input_tokens, LLMCallEvent.output_tokens).where(
                LLMCallEvent.workflow_id.in_([i for ids in wfs.values() for i in ids]), LLMCallEvent.kind == "chat"
            )
        ):
            per_wf[w] = per_wf.get(w, 0) + int(tin or 0) + int(tout or 0)
    per_run = {rid: sum(per_wf.get(i, 0) for i in ids) for rid, ids in wfs.items()}
    out = []
    for r in runs:
        d = r.detail or {}
        pr_runs = (
            {
                p.id: p
                for p in db.scalars(
                    select(PersonaRun).where(PersonaRun.id.in_([x["id"] for x in d.get("reviews", []) if x.get("id")]))
                )
            }
            if d.get("reviews")
            else {}
        )
        out.append(
            {
                "id": r.id,
                "event": r.event,
                "retry": retry_kind(r),
                "can_cancel": r.status == "started" and agent_controls.working(r),
                "trace": d.get("trace", []),
                "tokens": per_run.get(r.id, 0),
                "repo_id": r.repo_id,
                "title": r.title,
                "status": r.status,
                "reason": r.reason,
                "link": r.link,
                "created_at": r.created_at,
                "investigations": [inv[i] for i in (r.investigation_ids or []) if i in inv],
                "reviews": [
                    {**x, "ai": bool(x.get("model")), "present": x.get("id") in pr_runs} for x in d.get("reviews", [])
                ],
                "files": d.get("files", []),
                "skipped": d.get("skipped", []),
            }
        )
    return out


# ------------------------------------------------------------------------------------------------ the inbox
def reviews_of(db: Session, org_id: str, number: int) -> int:
    """How many times a pull request was reviewed (each new commit that was reviewed counts)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(
                AgentRun.org_id == org_id,
                AgentRun.event == PR,
                AgentRun.status == "started",
                AgentRun.event_key.like(f"{number}-%"),
            )
        )
        or 0
    )


PERSONA_NAMES = {"developer": "Developer", "security-auditor": "Security auditor"}
FOOTER = (
    "\n\n---\n_Written by an AI model through RepoLens and posted after a person approved it. "
    "It can be wrong: check each point against the code._"
)
MAX_COMMENT = 60_000  # GitHub allows 65,536 characters


def compose_review_comment(rows: list[PersonaRun], number: int, head: str) -> str:
    """The reviews of one pull request as one Markdown comment. Only what the reviews say: no new claims."""
    out = [f"### RepoLens review of #{number} at `{head[:7]}`", ""]
    models = sorted({r.model for r in rows if r.model})
    out.append(f"_AI generated by {', '.join(models) or 'a language model'}. Findings quote the code they are about._")
    for r in rows:
        out += ["", f"#### {PERSONA_NAMES.get(r.persona, r.persona)}", (r.summary or "").strip() or "No summary."]
        findings = r.findings or []
        if not findings:
            out.append("")
            out.append("No findings.")
        for f in findings:
            where = f"`{f.get('file')}:{f.get('line')}`" if f.get("line") else f"`{f.get('file')}`"
            out += ["", f"- **{f.get('severity', 'info')}**: {f.get('title', '')} ({where})"]
            if f.get("explanation"):
                out.append(f"  {f['explanation']}")
            if f.get("suggestion"):
                out.append(f"  Suggestion: {f['suggestion']}")
    return "\n".join(out)[:MAX_COMMENT]


def draft_review(
    db: Session, run: AgentRun, repo: Repository, full_name: str, number: int, head: str, persona_run_ids: list[str]
) -> None:
    rows = [r for r in (db.get(PersonaRun, i) for i in persona_run_ids) if r is not None]
    if not rows:
        return
    db.add(
        AgentDraft(
            org_id=run.org_id,
            repo_id=repo.id,
            run_id=run.id,
            kind="pr_comment",
            title=run.title,
            body=compose_review_comment(rows, number, head),
            meta={"repo": full_name, "number": number, "head": head, "link": run.link},
        )
    )
    db.commit()


def draft_investigations(db: Session, run: AgentRun, repo: Repository, ids: list[str]) -> None:
    if ids:
        db.add(
            AgentDraft(
                org_id=run.org_id,
                repo_id=repo.id,
                run_id=run.id,
                kind="investigation",
                title=run.title,
                meta={"link": run.link},
            )
        )
        db.commit()


def post_draft(db: Session, draft: AgentDraft, user_id: str, body: str | None = None) -> AgentDraft:
    """Put a drafted comment on its pull request. Called only from an admin's approval; the one place the agent writes
    to GitHub. A footer says an AI wrote it and a person approved it. On a refusal the draft stays, with the reason."""
    from ..api.github import make_client

    if draft.kind != "pr_comment":
        raise ValueError("Only a pull request comment can be posted")
    if (draft.meta or {}).get("pending"):
        raise ValueError("The investigation is still running, so the comment is not ready yet")
    text = (body if body is not None else draft.body).strip()
    if not text or len(text) > MAX_COMMENT:
        raise ValueError(f"The comment must be 1 to {MAX_COMMENT} characters")
    if FOOTER.strip() not in text:
        text += FOOTER
    try:
        url = make_client(db, draft.org_id).comment_pull(str(draft.meta["repo"]), int(draft.meta["number"]), text)
    except GitHubError as e:
        draft.error = str(e)[:400]
        db.commit()
        raise
    draft.body, draft.status, draft.posted_url, draft.error = text, "posted", url, ""
    draft.decided_by, draft.decided_at = user_id, datetime.now(UTC)
    db.add(
        AuditEvent(
            org_id=draft.org_id,
            actor_id=user_id,
            action="automation.comment_posted",
            target=str(draft.meta.get("repo", ""))[:200],
            allowed=True,
            detail={"pull_request": draft.meta.get("number"), "comment": url, "draft": draft.id},
        )
    )
    db.commit()
    return draft


def retry_kind(run: AgentRun) -> str | None:
    """What the button for this run says, or None when there is nothing to try again."""
    if run.status == "failed":
        return "try_again"
    if run.status == "skipped":
        return "run_anyway"
    if run.event == PR and run.status == "started" and not agent_controls.working(run):
        return "review_again"
    return None


def retry_run(db: Session, run: AgentRun, user_id: str) -> AgentRun:
    """Run one record again on purpose: a failed run again, a skipped one anyway (past its branch or daily limit), or a
    pull request that was reviewed once more. It reuses the record, so the same delivery is still handled only once;
    drafts the earlier try left in the inbox are dismissed so the newest review is the one waiting."""
    from .github import parse_github_repo

    repo = db.get(Repository, run.repo_id)
    full = parse_github_repo(repo.url) if repo else None
    kind = retry_kind(run)
    if repo is None or not full or kind is None:
        raise ValueError("This run cannot be tried again")
    target = int(run.event_key.split("-")[0])
    for d in db.scalars(select(AgentDraft).where(AgentDraft.run_id == run.id, AgentDraft.status.in_(("new", "read")))):
        db.delete(d)  # the newer try replaces it: an old review is not kept unless it was posted or archived
    run.status, run.reason = "started", "Trying again"
    run.detail = {"trace": (run.detail or {}).get("trace", []), "working": True}
    agent_controls.trace(run, f"An admin chose: {kind.replace('_', ' ')}")
    db.add(
        AuditEvent(
            org_id=run.org_id,
            actor_id=user_id,
            action="automation.retry",
            target=repo.name[:200],
            allowed=True,
            detail={"event": run.event, "how": kind, "link": run.link},
        )
    )
    db.commit()
    if run.event == PR:
        _submit(work_pr, run.id, full, target)
    else:
        _submit(work, run.id, full, target, MAX_PER_RUN)
    return run
