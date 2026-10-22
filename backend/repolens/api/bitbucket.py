import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import BitbucketConnection, Repository
from ..schemas import BitbucketConnectionIn
from ..security import decrypt_secret, encrypt_secret, redact_secrets
from ..services.access import scope_for
from ..services.bitbucket import BitbucketClient, BitbucketError
from ..services.jira import JiraError, validate_base_url
from ..services.policy import authorize

router = APIRouter(prefix="/api/bitbucket", tags=["bitbucket"])
_transport: httpx.BaseTransport | None = None  # tests inject a mock
_PART = re.compile(r"^[\w.~-]{1,100}$")


def _view(c: BitbucketConnection | None) -> dict:
    return {
        "connected": c is not None,
        "deployment": c.deployment if c else None,
        "base_url": (c.base_url or None) if c else None,
        "username": (c.username or None) if c else None,
    }


def make_client(db: Session, org_id: str) -> BitbucketClient:
    c = db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == org_id))
    if c is None:
        raise HTTPException(404, "Bitbucket is not configured")
    try:
        return BitbucketClient(
            c.deployment, decrypt_secret(c.token_encrypted), c.username, c.base_url, transport=_transport
        )
    except BitbucketError as e:
        raise HTTPException(400, str(e)) from None


def _call(fn):
    try:
        return fn()
    except BitbucketError as e:
        code = 404 if e.status == 404 else 502
        raise HTTPException(code, redact_secrets(str(e))) from None


@router.get("/connection")
def get_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _view(db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == p.org_id)))


@router.put("/connection")
def put_connection(
    body: BitbucketConnectionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    authorize(db, p, "github.configure", f"bitbucket-{body.deployment}")
    base = ""
    if body.deployment == "server":
        try:
            base = validate_base_url(body.base_url)
        except JiraError as e:
            raise HTTPException(400, str(e)) from None
    c = db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == p.org_id))
    if c is None:
        c = BitbucketConnection(org_id=p.org_id, token_encrypted="")
        db.add(c)
    c.deployment, c.base_url, c.username, c.token_encrypted = (
        body.deployment,
        base,
        body.username.strip(),
        encrypt_secret(body.token.strip()),
    )
    db.commit()
    return _view(c)


@router.delete("/connection", status_code=204)
def delete_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "github.configure", "bitbucket-disconnect")
    c = db.scalar(select(BitbucketConnection).where(BitbucketConnection.org_id == p.org_id))
    if c:
        db.delete(c)
        db.commit()


@router.post("/test")
def test_connection(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "github.configure", "bitbucket-test")
    try:
        who = make_client(db, p.org_id).whoami()
    except BitbucketError as e:
        return {"ok": False, "error": redact_secrets(str(e))}
    return {"ok": True, "user": who}


def _norm(url: str) -> str:
    from urllib.parse import urlparse

    u = urlparse(url.strip())
    return f"{(u.hostname or '').lower()}{u.path.lower().removesuffix('.git').rstrip('/')}"


@router.get("/repos")
def browse_repos(
    q: str = "",
    page: int = 1,
    per_page: int = 30,
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    client = make_client(db, p.org_id)
    items, truncated = _call(client.list_repos)
    scope = scope_for(db, p)
    added: dict[str, str] = {}
    for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id, Repository.source == "git")):
        if scope.can(r.id):
            added.setdefault(_norm(r.url), r.id)
    needle = q.strip().lower()
    rows = [x for x in items if not needle or needle in x["full_name"].lower() or needle in x["description"].lower()]
    per_page = max(1, min(per_page, 100))
    start = (max(page, 1) - 1) * per_page
    return {
        "total": len(rows),
        "page": page,
        "per_page": per_page,
        "truncated": truncated,
        "items": [
            {**x, "added_repo_id": added.get(_norm(x["clone_url"])) if x["clone_url"] else None}
            for x in rows[start : start + per_page]
        ],
    }


def _parts(owner: str, name: str) -> tuple[str, str]:
    if not (_PART.fullmatch(owner) and _PART.fullmatch(name)):
        raise HTTPException(400, "Invalid repository name")
    return owner, name


@router.get("/repos/{owner}/{name}/pulls")
def browse_pulls(
    owner: str, name: str, state: str = "open", p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    if state not in ("open", "closed", "all"):
        raise HTTPException(400, "state must be open, closed or all")
    o, n = _parts(owner, name)
    return _call(lambda: make_client(db, p.org_id).pulls(o, n, state))


@router.get("/repos/{owner}/{name}/branches")
def browse_branches(owner: str, name: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    o, n = _parts(owner, name)
    return _call(lambda: make_client(db, p.org_id).branches(o, n))
