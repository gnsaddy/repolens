from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, audit, audit_user, current_principal
from ..models import User
from ..schemas import InviteIn, ResetIn, TotpDisableIn, TotpEnableIn
from ..security import decrypt_secret, encrypt_secret, hash_password, secrets_random_password, verify_password
from ..services import account_mfa, auth_tokens, ratelimit, sessions, totp
from ..services.clientip import client_ip
from ..services.passwords import check_password
from ..services.policy import authorize

router = APIRouter(prefix="/api/auth", tags=["account"])


def _link(token: str) -> str:
    return f"/reset?token={token}"


def _person(u: User, pending: dict[str, str]) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "active": u.active,
        "auth_source": u.auth_source,
        "totp_enabled": u.totp_enabled,
        "pending": pending.get(u.id),  # "invite" | "reset" while a link is waiting to be used
        "created_at": u.created_at,
    }


@router.get("/people")
def people(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Everyone in the workspace with how they sign in (admins)."""
    authorize(db, p, "user.create", "list-people")
    pending = auth_tokens.pending_for(db, p.org_id)
    return [
        _person(u, pending) for u in db.scalars(select(User).where(User.org_id == p.org_id).order_by(User.created_at))
    ]


@router.post("/invites", status_code=201)
def invite(body: InviteIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Add a person who sets their own password through a one-time link (valid seven days)."""
    authorize(db, p, "user.create", body.email, via="invite", role=body.role)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That e-mail already has an account")
    u = User(
        org_id=p.org_id,
        email=email,
        name=body.name,
        role=body.role,
        password_hash=hash_password(secrets_random_password()),
    )
    db.add(u)
    db.commit()
    token, row = auth_tokens.issue(db, u, "invite", p.user_id)
    return {"person": _person(u, {u.id: "invite"}), "link": _link(token), "expires_at": row.expires_at}


def _target(db: Session, p: Principal, user_id: str) -> User:
    u = db.get(User, user_id)
    if u is None or u.org_id != p.org_id:
        raise HTTPException(404, "Person not found")
    return u


@router.post("/users/{user_id}/reset-link")
def reset_link(user_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """A one-time link (valid 24 hours) for someone who forgot their password. The old password keeps working until it is used."""
    u = _target(db, p, user_id)
    authorize(db, p, "user.create", u.email, via="reset-link")
    if u.auth_source != "password":
        raise HTTPException(400, "This person signs in through single sign-on; there is no password to reset")
    token, row = auth_tokens.issue(db, u, "reset", p.user_id)
    return {"link": _link(token), "expires_at": row.expires_at}


@router.post("/users/{user_id}/reset-mfa", status_code=204)
def reset_mfa(user_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Turn off someone's authenticator sign-in (they lost the device). They can set it up again."""
    u = _target(db, p, user_id)
    authorize(db, p, "user.create", u.email, via="reset-mfa")
    u.totp_enabled, u.totp_secret_encrypted, u.recovery_codes, u.totp_last_step = False, "", [], 0
    db.commit()
    sessions.revoke_others(db, u.id, None)


@router.post("/reset", status_code=204)
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    """Set a password with an invitation or reset link. Public: the link is the credential."""
    found = auth_tokens.redeem(db, body.token)
    if found is None:
        ratelimit.hit(f"badlink:{client_ip(request)}", 20)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This link is not valid or has expired. Ask an admin for a new one."
        )
    row, user = found
    problem = check_password(body.password, user.email)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user.password_hash = hash_password(body.password)
    row.used_at = datetime.now(UTC)
    db.commit()
    sessions.revoke_others(db, user.id, None)  # whoever held the old password is signed out
    audit_user(db, user, "auth.password_set", via=row.kind, ip=client_ip(request))


# ------------------------------------------------------------------------------------------------ authenticator app
@router.post("/totp/setup")
def totp_setup(
    body: TotpDisableIn | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """Start enabling an authenticator app: returns the secret to add to it. Nothing changes until a code is confirmed."""
    u = db.get(User, p.user_id)
    assert u is not None
    if u.auth_source != "password":
        raise HTTPException(400, "Your account signs in through single sign-on; use its own second factor")
    if u.totp_enabled:
        raise HTTPException(409, "An authenticator app is already set up. Turn it off first to change it.")
    secret = totp.new_secret()
    u.totp_secret_encrypted = encrypt_secret(secret)
    db.commit()
    return {"secret": secret, "uri": totp.uri(secret, u.email)}


@router.post("/totp/enable")
def totp_enable(body: TotpEnableIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Confirm with a code from the app; returns eight one-time recovery codes, shown only now."""
    u = db.get(User, p.user_id)
    assert u is not None
    if not u.totp_secret_encrypted or u.totp_enabled:
        raise HTTPException(409, "Start the setup first")
    step = totp.verify(decrypt_secret(u.totp_secret_encrypted), body.code, 0)
    if step is None:
        raise HTTPException(400, "That code is not right. Check the time on your phone and try the next code.")
    codes = totp.new_recovery_codes()
    u.totp_enabled, u.totp_last_step, u.recovery_codes = True, step, [totp.hash_code(c) for c in codes]
    db.commit()
    audit(db, p, "auth.mfa_enabled", u.email)
    return {"recovery_codes": codes}


@router.post("/totp/disable", status_code=204)
def totp_disable(body: TotpDisableIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Turn the authenticator off. Needs the password and a current code (or a recovery code)."""
    u = db.get(User, p.user_id)
    assert u is not None
    if not verify_password(body.password, u.password_hash):
        raise HTTPException(400, "Password is incorrect")
    if not u.totp_enabled or not account_mfa.accept(db, u, body.code):
        raise HTTPException(400, "That code is not valid")
    u.totp_enabled, u.totp_secret_encrypted, u.recovery_codes, u.totp_last_step = False, "", [], 0
    db.commit()
    audit(db, p, "auth.mfa_disabled", u.email)
