import logging
import subprocess
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, session_factory
from ..deps import Principal, audit, current_principal
from ..models import GithubConnection, IndexRun, OAuthFlow, PullRequest, Repository
from ..schemas import GithubConnectionIn, OAuthPollIn, OAuthStartIn, SyncPullsIn
from ..security import decrypt_secret, encrypt_secret, redact_secrets
from ..services.github import (
    GitHubClient,
    GitHubError,
    device_poll,
    device_start,
    parse_github_repo,
    validate_api_base,
)
from ..services.policy import authorize
from ..services.pulls import sync_pulls
from .repos import _get_repo

log = logging.getLogger("repolens.github")
router = APIRouter(prefix="/api", tags=["github"])

# Overridable in tests to inject a mock transport.
_transport: httpx.BaseTransport | None = None


def make_client(db: Session, org_id: str) -> GitHubClient:
    c = db.scalar(select(GithubConnection).where(GithubConnection.org_id == org_id))
    if c is None:
        raise HTTPException(404, "GitHub is not configured")
    return GitHubClient(decrypt_secret(c.token_encrypted), c.api_base, transport=_transport)


def resolve_gh_repo(repo: Repository, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    found = parse_github_repo(repo.url)
    if found or repo.source != "local":
        return found
    try:  # local checkout: use its origin remote
        out = subprocess.run(
            ["git", "-C", repo.url, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10
        )
        return parse_github_repo(out.stdout) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _conn_view(c: GithubConnection | None) -> dict:
    return {
        "connected": c is not None,
        "api_base": c.api_base if c else None,
        "auth_method": c.auth_method if c else None,
        "login": (c.login or None) if c else None,
        "scope": (c.scope or None) if c else None,
        "oauth_client_id": (c.client_id if c and c.client_id else get_settings().github_client_id) or None,
    }


@router.get("/github/connection")
def get_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _conn_view(db.scalar(select(GithubConnection).where(GithubConnection.org_id == p.org_id)))


@router.put("/github/connection")
def put_connection(body: GithubConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "github.configure", body.api_base)
    try:
        base = validate_api_base(body.api_base)
    except GitHubError as e:
        raise HTTPException(400, str(e)) from e
    c = db.scalar(select(GithubConnection).where(GithubConnection.org_id == p.org_id))
    if c is None:
        c = GithubConnection(org_id=p.org_id, token_encrypted="")
        db.add(c)
    c.api_base, c.token_encrypted, c.auth_method, c.login, c.scope = base, encrypt_secret(body.token), "token", "", ""
    db.commit()
    return _conn_view(c)


# ---------------------------------------------------------------------------------------- OAuth device flow
@router.post("/github/oauth/device/start")
def oauth_start(body: OAuthStartIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Step 1: ask GitHub for a user code. The admin opens `verification_uri` and enters `user_code`."""
    authorize(db, p, "github.configure", "oauth-start")
    existing = db.scalar(select(GithubConnection).where(GithubConnection.org_id == p.org_id))
    client_id = body.client_id or (existing.client_id if existing else "") or get_settings().github_client_id
    if not client_id:
        raise HTTPException(400, "Enter the Client ID of a GitHub OAuth App with Device Flow enabled")
    try:
        base = validate_api_base(body.api_base)
        d = device_start(client_id, body.scope, base, _transport)
    except GitHubError as e:
        raise HTTPException(502, redact_secrets(str(e))) from e
    flow = OAuthFlow(
        org_id=p.org_id,
        user_id=p.user_id,
        client_id=client_id,
        scope=body.scope,
        api_base=base,
        device_code_encrypted=encrypt_secret(d["device_code"]),
        interval_s=int(d.get("interval", 5)),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(d.get("expires_in", 900))),
    )
    db.add(flow)
    db.commit()
    return {
        "flow_id": flow.id,
        "user_code": d["user_code"],
        "verification_uri": d["verification_uri"],
        "expires_in": int(d.get("expires_in", 900)),
        "interval": flow.interval_s,
    }


@router.post("/github/oauth/device/poll")
def oauth_poll(body: OAuthPollIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Step 2: poll until the user approves. On success the token is stored encrypted; it is never returned."""
    authorize(db, p, "github.configure", "oauth-poll")
    flow = db.get(OAuthFlow, body.flow_id)
    if flow is None or flow.org_id != p.org_id or flow.user_id != p.user_id:
        raise HTTPException(404, "Unknown or expired authorisation")
    expires = flow.expires_at if flow.expires_at.tzinfo else flow.expires_at.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        db.delete(flow)
        db.commit()
        return {"status": "expired"}
    try:
        d = device_poll(flow.client_id, decrypt_secret(flow.device_code_encrypted), flow.api_base, _transport)
    except GitHubError as e:
        return {"status": "error", "error": redact_secrets(str(e)), "interval": flow.interval_s}
    err = d.get("error")
    if err == "authorization_pending":
        return {"status": "pending", "interval": flow.interval_s}
    if err == "slow_down":
        flow.interval_s = int(d.get("interval", flow.interval_s + 5))
        db.commit()
        return {"status": "pending", "interval": flow.interval_s}
    if err in ("expired_token", "access_denied", "incorrect_device_code"):
        db.delete(flow)
        db.commit()
        return {"status": "denied" if err == "access_denied" else "expired"}
    if err or "access_token" not in d:
        return {
            "status": "error",
            "error": redact_secrets(str(d.get("error_description") or err or "unexpected response")),
        }
    token = d["access_token"]
    login = ""
    try:
        login = GitHubClient(token, flow.api_base, transport=_transport).whoami().get("login", "")
    except GitHubError:
        pass  # the token still works for PR sync; the login is only a label
    c = db.scalar(select(GithubConnection).where(GithubConnection.org_id == p.org_id))
    if c is None:
        c = GithubConnection(org_id=p.org_id, token_encrypted="")
        db.add(c)
    c.api_base, c.token_encrypted, c.auth_method = flow.api_base, encrypt_secret(token), "oauth"
    c.client_id, c.login, c.scope = flow.client_id, login, d.get("scope", flow.scope)
    db.delete(flow)
    db.commit()
    audit(db, p, "github.oauth.connected", login, scope=c.scope)
    return {"status": "success", **_conn_view(c)}


@router.delete("/github/connection", status_code=204)
def delete_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "github.configure", "delete")
    c = db.scalar(select(GithubConnection).where(GithubConnection.org_id == p.org_id))
    if c:
        db.delete(c)
        db.commit()


@router.post("/github/test")
def test_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "github.configure", "test")
    try:
        me = make_client(db, p.org_id).whoami()
    except GitHubError as e:
        return {"ok": False, "error": redact_secrets(str(e))}
    return {"ok": True, "user": me.get("login")}


def run_sync(repo_id: str, run_id: str, gh_repo: str, limit: int, only: list[int] | None = None) -> None:
    db = session_factory()()
    try:
        repo = db.get(Repository, repo_id)
        run = db.get(IndexRun, run_id)
        assert repo is not None and run is not None
        try:
            res = sync_pulls(db, repo, make_client(db, repo.org_id), gh_repo, limit, only)
            run.status, run.chunks_written = "completed", res["synced"]
            if only:  # a webhook-triggered sync: assess the PR so reviewers see its risk right away
                _assess_synced(db, repo, only)
        except Exception as e:  # keep the failure visible on the run, never crash the worker
            db.rollback()
            log.exception("PR sync failed for %s", repo_id)
            run = db.get(IndexRun, run_id)
            assert run is not None
            run.status, run.error = "failed", redact_secrets(f"{type(e).__name__}: {e}")[:500]
        from datetime import UTC, datetime

        run.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def schedule_sync(db: Session, repo: Repository, gh_repo: str, limit: int, only: list[int] | None = None) -> IndexRun:
    run = IndexRun(repo_id=repo.id, status="running", commit_sha="pull-requests")
    db.add(run)
    db.commit()
    if get_settings().run_jobs_inline:
        run_sync(repo.id, run.id, gh_repo, limit, only)
    else:
        from .repos import _pool

        _pool.submit(run_sync, repo.id, run.id, gh_repo, limit, only)
    return run


@router.post("/repositories/{repo_id}/sync-prs", status_code=202)
def sync_prs(
    repo_id: str,
    body: SyncPullsIn | None = None,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    repo = _get_repo(db, p, repo_id)
    body = body or SyncPullsIn()
    if repo.source == "docs":
        raise HTTPException(400, "Document collections have no pull requests")
    gh_repo = resolve_gh_repo(repo, body.github_repo)
    authorize(db, p, "github.sync", repo.name, github_repo=gh_repo or "")
    if not gh_repo:
        raise HTTPException(400, "Could not determine the GitHub repository; pass github_repo as 'owner/name'")
    make_client(db, p.org_id)  # 404 if not configured
    run = schedule_sync(db, repo, gh_repo, body.limit)
    return {"run_id": run.id, "github_repo": gh_repo}


@router.get("/repositories/{repo_id}/pulls")
def list_pulls(repo_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _get_repo(db, p, repo_id)
    rows = db.scalars(
        select(PullRequest).where(PullRequest.repo_id == repo_id).order_by(PullRequest.number.desc()).limit(100)
    )
    return [
        {
            "number": r.number,
            "title": r.title,
            "state": r.state,
            "author": r.author,
            "url": r.url,
            "files": len(r.files),
            "commits": len(r.commit_shas),
            "jira_keys": r.jira_keys,
            "labels": r.labels,
            "merged_at": r.merged_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _assess_synced(db: Session, repo: Repository, numbers: list[int]) -> None:
    from ..services import risk as oracle
    from .risk import persist

    for n in numbers:
        try:
            persist(db, repo.org_id, repo.id, "webhook", oracle.assess(db, oracle.from_pr(db, repo, n)))
        except Exception:  # risk scoring must never fail the sync itself
            log.exception("risk assessment failed for PR %s", n)
