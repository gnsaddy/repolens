import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Repository
from .repos import schedule_index

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _norm(url: str) -> str:
    return url.lower().removesuffix(".git").replace("git@github.com:", "https://github.com/")


@router.post("/github")
async def github(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
    db: Session = Depends(get_db),
):
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(503, "GitHub webhook secret not configured")
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    if x_github_event == "pull_request" and payload.get("action") in (
        "opened",
        "edited",
        "closed",
        "reopened",
        "synchronize",
        "ready_for_review",
    ):
        return _on_pull_request(db, payload)
    if x_github_event != "push":
        return {"ignored": x_github_event}
    clone_url = _norm(payload.get("repository", {}).get("clone_url", ""))
    matched = [r for r in db.scalars(select(Repository).where(Repository.source == "git")) if _norm(r.url) == clone_url]
    for r in matched:
        r.status = "indexing"
        db.commit()
        schedule_index(r.id)
    return {"reindexing": [r.id for r in matched]}


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
    return {"pr_synced": synced}
