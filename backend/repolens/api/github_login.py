from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import GithubLoginConfig, SsoConfig
from ..services import github_login as gl
from ..services.policy import authorize

public = APIRouter(prefix="/api/auth/github", tags=["github-login"])
admin = APIRouter(prefix="/api/settings/github-login", tags=["github-login"])


def redirect_uri(request: Request) -> str:
    base = get_settings().public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/api/auth/github/callback"


# ------------------------------------------------------------------------------------------------ sign-in
@public.get("/enabled")
def enabled(db: Session = Depends(get_db)):
    """Should the sign-in page offer GitHub? Reveals nothing but yes or no."""
    return {"enabled": gl.available(db)}


@public.get("/start")
def start(request: Request, db: Session = Depends(get_db)):
    try:
        url = gl.begin(db, redirect_uri(request))
    except gl.SsoError as e:
        raise HTTPException(404, str(e)) from None
    return RedirectResponse(url, status_code=302)


@public.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    """GitHub sends the browser back here. Failures go to the sign-in page with a short, safe message."""

    def fail(msg: str) -> RedirectResponse:
        return RedirectResponse(f"/login?sso_error={quote(msg[:200])}", status_code=302)

    if error or not state or not code:
        return fail("GitHub did not complete the sign-in")
    try:
        gl.complete(db, state, code, redirect_uri(request))
        login_code = gl.mint_login_code(db, state)
    except gl.SsoError as e:
        db.rollback()
        return fail(str(e))
    return RedirectResponse(f"/sso-complete?code={login_code}", status_code=302)


# ------------------------------------------------------------------------------------------------ admin settings
class GithubLoginIn(BaseModel):
    allowed_domains: str | list[str] = ""  # domains whose people get an account created; empty: existing accounts only
    default_role: str = Field(default="viewer", pattern="^(viewer|member)$")
    enabled: bool = True


def _view(request: Request, cfg: GithubLoginConfig | None) -> dict:
    base = {"app_configured": gl.app_configured(), "redirect_uri": redirect_uri(request)}
    if cfg is None:
        return {"configured": False, **base}
    return {
        "configured": True,
        "allowed_domains": cfg.allowed_domains,
        "default_role": cfg.default_role,
        "auto_provision": cfg.auto_provision,
        "enabled": cfg.enabled,
        **base,
    }


@admin.get("")
def get_config(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "github-login-view")
    return _view(request, db.scalar(select(GithubLoginConfig).where(GithubLoginConfig.org_id == p.org_id)))


@admin.put("")
def put_config(
    body: GithubLoginIn, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    authorize(db, p, "sso.configure", "github-login")
    blank = not (body.allowed_domains.strip() if isinstance(body.allowed_domains, str) else body.allowed_domains)
    try:
        domains = [] if blank else gl.parse_domains(body.allowed_domains)
    except gl.SsoError as e:
        raise HTTPException(400, str(e)) from None
    taken = [
        {d.lower() for d in c.allowed_domains}
        for c in (
            *db.scalars(select(GithubLoginConfig).where(GithubLoginConfig.org_id != p.org_id)),
            *db.scalars(select(SsoConfig).where(SsoConfig.org_id != p.org_id)),
        )
    ]
    clash = set(domains) & set().union(*taken) if taken else set()
    if clash:  # one e-mail domain belongs to one organisation, or sign-in would be ambiguous
        raise HTTPException(409, f"The domain {sorted(clash)[0]} is already used by another organisation")
    cfg = db.scalar(select(GithubLoginConfig).where(GithubLoginConfig.org_id == p.org_id))
    if cfg is None:
        cfg = GithubLoginConfig(org_id=p.org_id)
        db.add(cfg)
    cfg.allowed_domains, cfg.default_role = domains, body.default_role
    cfg.auto_provision, cfg.enabled = bool(domains), body.enabled
    db.commit()
    return _view(request, cfg)


@admin.delete("", status_code=204)
def delete_config(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "github-login-delete")
    cfg = db.scalar(select(GithubLoginConfig).where(GithubLoginConfig.org_id == p.org_id))
    if cfg:
        db.delete(cfg)
        db.commit()
