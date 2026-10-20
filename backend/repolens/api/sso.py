from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Organization, SsoConfig, User
from ..schemas import TokenOut, UserOut
from ..security import create_token, encrypt_secret
from ..services import sso
from ..services.policy import authorize

public = APIRouter(prefix="/api/auth/sso", tags=["sso"])
admin = APIRouter(prefix="/api/settings/sso", tags=["sso"])


def redirect_uri(request: Request) -> str:
    base = get_settings().public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/api/auth/sso/callback"


def _config_for_email(db: Session, email: str) -> SsoConfig | None:
    domain = email.strip().lower().rsplit("@", 1)[-1] if "@" in email else ""
    if not domain:
        return None
    for cfg in db.scalars(select(SsoConfig).where(SsoConfig.enabled.is_(True))):
        if domain in [d.lower() for d in cfg.allowed_domains]:
            return cfg
    return None


# ------------------------------------------------------------------------------------------------ sign-in
@public.get("/discover")
def discover(email: str = Query(max_length=320), db: Session = Depends(get_db)):
    """Does this e-mail address sign in with single sign-on? Reveals nothing but yes/no and the organisation name."""
    cfg = _config_for_email(db, email)
    if cfg is None:
        return {"sso": False}
    org = db.get(Organization, cfg.org_id)
    return {"sso": True, "organization": org.name if org else ""}


@public.get("/start")
def start(request: Request, email: str = Query(max_length=320), db: Session = Depends(get_db)):
    cfg = _config_for_email(db, email)
    if cfg is None:
        raise HTTPException(404, "Single sign-on is not set up for that e-mail address")
    try:
        url = sso.begin(db, cfg, redirect_uri(request))
    except sso.SsoError as e:
        raise HTTPException(502, str(e)) from None
    return RedirectResponse(url, status_code=302)


@public.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    """The provider sends the browser back here. Failures go to the login page with a short, safe message."""

    def fail(msg: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"/login?sso_error={quote(msg[:200])}", status_code=302)

    if error or not state or not code:
        return fail("The identity provider did not complete the sign-in")
    try:
        sso.complete(db, state, code, redirect_uri(request))
        login_code = sso.mint_login_code(db, state)
    except sso.SsoError as e:
        db.rollback()
        return fail(str(e))
    return RedirectResponse(f"/sso-complete?code={login_code}", status_code=302)


class ExchangeIn(BaseModel):
    code: str = Field(min_length=20, max_length=100)


@public.post("/exchange", response_model=TokenOut)
def exchange(body: ExchangeIn, db: Session = Depends(get_db)):
    """Trade the one-time code for an application token (so no token ever appears in a URL)."""
    try:
        user = sso.redeem_login_code(db, body.code)
    except sso.SsoError as e:
        raise HTTPException(401, str(e)) from None
    return TokenOut(access_token=create_token(user.id, user.org_id, user.role), user=UserOut.model_validate(user))


# ------------------------------------------------------------------------------------------------ admin settings
class SsoIn(BaseModel):
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(default="", max_length=2000)  # empty keeps the stored secret
    allowed_domains: str | list[str]
    default_role: str = Field(default="viewer", pattern="^(viewer|member)$")
    auto_provision: bool = True
    enabled: bool = True


def _view(request: Request, cfg: SsoConfig | None) -> dict:
    uri = redirect_uri(request)
    if cfg is None:
        return {"configured": False, "redirect_uri": uri}
    return {
        "configured": True,
        "issuer": cfg.issuer,
        "client_id": cfg.client_id,
        "has_secret": bool(cfg.client_secret_encrypted),
        "allowed_domains": cfg.allowed_domains,
        "default_role": cfg.default_role,
        "auto_provision": cfg.auto_provision,
        "enabled": cfg.enabled,
        "redirect_uri": uri,
    }


@admin.get("")
def get_sso(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "view")
    return _view(request, db.scalar(select(SsoConfig).where(SsoConfig.org_id == p.org_id)))


@admin.put("")
def put_sso(body: SsoIn, request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", body.issuer)
    try:
        domains = sso.parse_domains(body.allowed_domains)
        issuer = body.issuer.strip().rstrip("/")
        sso.discover(issuer, fresh=True)  # proves the issuer is reachable and well formed before saving
    except sso.SsoError as e:
        raise HTTPException(400, str(e)) from None
    for other in db.scalars(select(SsoConfig).where(SsoConfig.org_id != p.org_id)):
        clash = set(domains) & {d.lower() for d in other.allowed_domains}
        if clash:  # one e-mail domain can belong to one organisation, or sign-in would be ambiguous
            raise HTTPException(409, f"The domain {sorted(clash)[0]} is already used by another organisation")
    cfg = db.scalar(select(SsoConfig).where(SsoConfig.org_id == p.org_id))
    if cfg is None:
        if not body.client_secret:
            raise HTTPException(400, "A client secret is required")
        cfg = SsoConfig(org_id=p.org_id, issuer=issuer, client_id=body.client_id.strip())
        db.add(cfg)
    cfg.issuer, cfg.client_id = issuer, body.client_id.strip()
    if body.client_secret:
        cfg.client_secret_encrypted = encrypt_secret(body.client_secret)
    cfg.allowed_domains, cfg.default_role = domains, body.default_role
    cfg.auto_provision, cfg.enabled = body.auto_provision, body.enabled
    db.commit()
    return _view(request, cfg)


@admin.delete("", status_code=204)
def delete_sso(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "delete")
    cfg = db.scalar(select(SsoConfig).where(SsoConfig.org_id == p.org_id))
    if cfg:
        db.delete(cfg)
        db.commit()


@admin.get("/users")
def sso_users(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "sso.configure", "users")
    rows = db.scalars(select(User).where(User.org_id == p.org_id, User.auth_source != "password").order_by(User.email))
    return [
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": u.active, "source": u.auth_source}
        for u in rows
    ]
