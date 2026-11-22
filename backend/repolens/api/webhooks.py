import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Repository
from ..services import webhook_log
from .repos import schedule_index

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
PR_ACTIONS = ("opened", "edited", "closed", "reopened", "synchronize", "ready_for_review")


def _norm(url: str) -> str:
    return url.lower().removesuffix(".git").replace("git@github.com:", "https://github.com/")


def _orgs(db: Session, payload: dict) -> list[str]:
    """The workspaces that have the repository this delivery is about."""
    from ..services import automations

    return sorted({r.org_id for r in automations._repos(db, payload)})


def signature_of(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@router.post("/github")
async def github(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    db: Session = Depends(get_db),
):
    secret = get_settings().github_webhook_secret
    if not secret:
        webhook_log.record(
            db,
            org_ids=None,
            delivery_id=x_github_delivery,
            event=x_github_event,
            outcome="rejected",
            note="No webhook secret is set on the server (REPOLENS_GITHUB_WEBHOOK_SECRET)",
        )
        raise HTTPException(503, "GitHub webhook secret not configured")
    body = await request.body()
    if not hmac.compare_digest(signature_of(secret, body), x_hub_signature_256):
        webhook_log.record(
            db,
            org_ids=None,
            delivery_id=x_github_delivery,
            event=x_github_event,
            outcome="rejected",
            note="The signature did not match: the secret on GitHub differs from the one on the server",
        )
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    return handle(db, x_github_event, payload, x_github_delivery)


def handle(db: Session, event: str, payload: dict, delivery_id: str = "", org_ids: list[str] | None = None) -> dict:
    """Act on a delivery whose signature was checked, and keep a one line record of what was done."""
    action = str(payload.get("action") or "")
    full = str((payload.get("repository") or {}).get("full_name") or "")
    orgs = org_ids if org_ids is not None else _orgs(db, payload)

    def done(result: dict, outcome: str = "ok", note: str = "") -> dict:
        webhook_log.record(
            db,
            org_ids=orgs,
            delivery_id=delivery_id,
            event=event,
            action=action,
            repo=full,
            outcome=outcome,
            note=note or webhook_log.note_of(result),
        )
        return result

    try:
        if event == "selftest":
            return done(
                {"pong": True}, note="The secret is set and the receiver works (a self test, not sent by GitHub)"
            )
        if event == "ping":
            return done({"pong": True}, note="GitHub can reach RepoLens and the secret matches")
        if event == "pull_request" and action in PR_ACTIONS:
            return done(_on_pull_request(db, payload))
        if event == "workflow_run":
            from ..services import automations

            return done(automations.on_workflow_run(db, payload))
        if event == "issue_comment":
            from ..services import agent_commands

            return done(agent_commands.on_issue_comment(db, payload))
        if event != "push":
            return done({"ignored": event}, outcome="ignored", note=f"RepoLens does not use {event} events")
        clone_url = _norm(payload.get("repository", {}).get("clone_url", ""))
        matched = [
            r for r in db.scalars(select(Repository).where(Repository.source == "git")) if _norm(r.url) == clone_url
        ]
        for r in matched:
            r.status = "indexing"
            db.commit()
            schedule_index(r.id)
        if not matched:
            return done({"reindexing": []}, outcome="ignored", note="No repository added to RepoLens has this address")
        return done({"reindexing": [r.id for r in matched]})
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        webhook_log.record(
            db,
            org_ids=orgs,
            delivery_id=delivery_id,
            event=event,
            action=action,
            repo=full,
            outcome="error",
            note=f"Failed: {type(e).__name__}",
        )
        raise


def _on_pull_request(db: Session, payload: dict) -> dict:
    from .github import schedule_sync

    clone_url = _norm(payload.get("repository", {}).get("clone_url", ""))
    number = (payload.get("pull_request") or {}).get("number")
    full = (payload.get("repository") or {}).get("full_name")
    matched = [r for r in db.scalars(select(Repository).where(Repository.source == "git")) if _norm(r.url) == clone_url]
    synced = []
    for r in matched:
        if number and full:
            try:
                schedule_sync(db, r, full, 1, [int(number)])
                synced.append(r.id)
            except Exception:  # no GitHub connection for that org: nothing to do
                continue
    from ..services import automations

    return {"pr_synced": synced, **automations.on_pull_request(db, payload)}
