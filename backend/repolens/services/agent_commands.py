"""Commands written in a pull request comment: `/repolens review` and `/repolens ask <question>`.

Only a person GitHub says is the repository's owner, a member of its organisation or a collaborator can use them, and a
bot never can. They run under the same rule as reviews of new pull requests (the "Review new pull requests" switch, its
daily limit, the pause and the token budget), and like everything the agent makes they end as a draft in the Inbox: the
answer is posted on the pull request only when an admin approves it."""

import re

from sqlalchemy.orm import Session

from ..models import AgentDraft, AgentRun
from . import agent_controls
from . import automations as auto
from .access import scope_for_user_id

TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")  # GitHub's author_association values that may give a command
COMMAND = re.compile(r"^\s*/repolens\s+(review|ask)\b[ \t]*(.*)$", re.I)
MAX_QUESTION = 500


def parse(body: str) -> tuple[str, str] | None:
    """`(command, rest)` for the first line of a comment that gives one, else None."""
    for line in (body or "").splitlines():
        if line.strip():
            m = COMMAND.match(line)
            return (m.group(1).lower(), m.group(2).strip()) if m else None
    return None


def on_issue_comment(db: Session, payload: dict) -> dict:
    """A comment was written on an issue or pull request: run it as a command when it is one from a trusted person."""
    if payload.get("action") != "created":
        return {"command": "ignored"}
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    full = (payload.get("repository") or {}).get("full_name") or ""
    number = issue.get("number")
    if not issue.get("pull_request") or not number or not full:
        return {"command": "ignored"}  # a comment on an issue, not a pull request
    found = parse(str(comment.get("body") or ""))
    if found is None:
        return {"command": "none"}
    if (payload.get("sender") or {}).get("type") == "Bot" or (comment.get("user") or {}).get("type") == "Bot":
        return {"command": "ignored", "why": "from a bot"}
    if comment.get("author_association") not in TRUSTED:
        return {"command": "ignored", "why": "not from an owner, member or collaborator"}
    cmd, rest = found
    if cmd == "ask" and not rest:
        return {"command": "ignored", "why": "no question"}
    cid = str(comment.get("id") or "")
    link = str(comment.get("html_url") or issue.get("html_url") or "")[:500]
    who = str((comment.get("user") or {}).get("login") or "someone")[:60]
    started: list[str] = []
    for repo in auto._repos(db, payload):
        rule = auto.rule_for(db, repo.org_id, repo.id, auto.PR)
        if rule is None:
            continue
        if cmd == "review":
            key, title = f"{number}-c{cid}", f"PR #{number}: {str(issue.get('title') or '')}"[:300]
        else:
            key, title = f"ask-{number}-{cid}", f"Question on PR #{number}: {rest}"[:300]
        if auto._seen(db, repo.org_id, auto.PR, key):
            continue
        why = agent_controls.gate(db, repo.org_id)
        if not why and auto.used_today(db, repo.org_id, auto.PR) >= rule.daily_limit:
            why = f"The limit of {rule.daily_limit} reviewed pull requests a day is used"
        if not why and cmd == "review" and auto.reviews_of(db, repo.org_id, int(number)) >= auto.MAX_REVIEWS_PER_PR:
            why = f"Already reviewed {auto.MAX_REVIEWS_PER_PR} times"
        if why:
            auto._skip(db, repo.org_id, rule, repo, auto.PR, key, why, title, link)
            continue
        run = AgentRun(
            org_id=repo.org_id,
            rule_id=rule.id,
            repo_id=repo.id,
            event=auto.PR,
            event_key=key,
            status="started",
            reason="Reading the changed files" if cmd == "review" else "Looking for the answer",
            title=title,
            link=link,
        )
        agent_controls.mark_working(run)
        agent_controls.trace(
            run, f"{who} wrote `/repolens {cmd}` on pull request #{number} ({comment.get('author_association')})."
        )
        if cmd == "ask":
            run.detail = {**(run.detail or {}), "question": rest[:MAX_QUESTION]}
        db.add(run)
        db.commit()
        if cmd == "review":
            auto._submit(auto.work_pr, run.id, full, int(number))
        else:
            auto._submit(work_ask, run.id, full, int(number), rest[:MAX_QUESTION])
        started.append(run.id)
    return {"command": cmd, "started": started}


def work_ask(agent_run_id: str, full_name: str, number: int, question: str) -> None:
    """Answer a question from the repository's index and leave the answer as a draft. Never raises."""
    from ..db import session_factory

    with session_factory()() as db:
        run = db.get(AgentRun, agent_run_id)
        if run is None:
            return
        try:
            _work_ask(db, run, full_name, number, question)
        except Exception as e:
            auto.log.exception("pull request question failed")
            db.rollback()
            auto._finish(db, run, "failed", f"Could not answer the question ({type(e).__name__})")


def _work_ask(db: Session, run: AgentRun, full_name: str, number: int, question: str) -> None:
    from . import knowledge

    who = auto._who(db, run)
    if who is None:
        return
    rule, repo, _runner = who
    if not auto.model_info(db, run.org_id)["ai"]:
        auto._finish(db, run, "skipped", "No language model is connected, so there is no AI answer.")
        return
    out = knowledge.ask(db, run.org_id, question, [repo.id], scope=scope_for_user_id(db, rule.created_by), fresh=True)
    if out.get("mode") != "llm":
        auto._finish(db, run, "failed", f"The model could not write an answer ({out.get('mode')})")
        return
    if agent_controls.cancelled(db, run):
        return
    files = sorted({str(s.get("file")) for s in out.get("sources", []) if s.get("file")})[:8]
    body = f"### RepoLens answer\n\n> {question}\n\n{str(out.get('answer') or '').strip()}"
    if files:
        body += "\n\nFrom: " + ", ".join(f"`{f}`" for f in files)
    db.add(
        AgentDraft(
            org_id=run.org_id,
            repo_id=repo.id,
            run_id=run.id,
            kind="pr_comment",
            title=run.title,
            body=body[: auto.MAX_COMMENT],
            meta={"repo": full_name, "number": number, "head": "", "link": run.link},
        )
    )
    db.commit()
    auto._finish(
        db,
        run,
        "started",
        f"Answered from {len(files)} file(s); the draft is waiting in the Inbox",
        detail={"kind": auto.PR, "pull_request": number, "question": question, "files": files, "reviews": []},
    )
