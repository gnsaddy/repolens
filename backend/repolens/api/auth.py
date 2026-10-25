from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import AuditEvent, Organization, User, UserSession
from ..schemas import LoginIn, PasswordChange, ProfilePatch, RegisterIn, TokenOut, UserCreate, UserOut
from ..security import hash_password, verify_password
from ..services import account_mfa, ratelimit, sessions
from ..services.clientip import client_ip
from ..services.passwords import check_password
from ..services.policy import authorize

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _strong(password: str, email: str) -> None:
    problem = check_password(password, email)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    first = db.scalar(select(User.id).limit(1)) is None  # the very first workspace can always be created
    if not first and not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is closed. Ask an admin for an invitation.")
    email = body.email.lower()
    _strong(body.password, email)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    org = Organization(name=body.organization)
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=email, name=body.name, password_hash=hash_password(body.password), role="admin")
    db.add(user)
    db.commit()
    return TokenOut(access_token=sessions.start(db, user, request), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    cfg = get_settings()
    key = f"{body.email.lower()}|{client_ip(request)}"
    wait = ratelimit.blocked_for(key, cfg.login_max_failures, cfg.login_window_seconds)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed sign-ins. Try again in {max(1, wait // 60)} min.",
            headers={"Retry-After": str(wait)},
        )
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.active:
        ratelimit.fail(key, cfg.login_window_seconds)
        if user:  # a trail for investigating guessing; the password itself is never recorded
            db.add(
                AuditEvent(
                    org_id=user.org_id,
                    actor_id=user.id,
                    action="auth.login_failed",
                    target=user.email[:200],
                    allowed=False,
                    detail={"ip": client_ip(request), "active": user.active},
                )
            )
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user.totp_enabled:  # second step: an authenticator code, or one of the one-time recovery codes
        if not body.code.strip():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication code required")
        if not account_mfa.accept(db, user, body.code):
            ratelimit.fail(key, cfg.login_window_seconds)
            db.add(
                AuditEvent(
                    org_id=user.org_id,
                    actor_id=user.id,
                    action="auth.mfa_failed",
                    target=user.email[:200],
                    allowed=False,
                    detail={"ip": client_ip(request)},
                )
            )
            db.commit()
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "That code is not valid. Try the next one or a recovery code."
            )
    ratelimit.clear(key)
    return TokenOut(access_token=sessions.start(db, user, request), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.get(User, p.user_id)


@router.get("/users", response_model=list[UserOut])
def list_users(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.org_id == p.org_id)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "user.create", body.email)
    _strong(body.password, body.email)
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    u = User(
        org_id=p.org_id,
        email=body.email.lower(),
        name=body.name,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(u)
    db.commit()
    return u


def _session_only(p: Principal) -> None:
    """Account security (password, sessions, profile) needs a real sign-in; a personal access token is for tools."""
    if p.token_scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in to change account settings; access tokens cannot")


@router.patch("/me", response_model=UserOut)
def update_profile(body: ProfilePatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _session_only(p)
    user = db.get(User, p.user_id)
    assert user is not None
    user.name = body.name.strip()
    db.commit()
    return user


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Change your password. Every other session is signed out."""
    _session_only(p)
    user = db.get(User, p.user_id)
    assert user is not None
    if user.auth_source != "password":
        raise HTTPException(400, "Your account signs in through single sign-on; there is no password to change")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    _strong(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    sessions.revoke_others(db, user.id, p.session_id)


def _session_view(s: UserSession, current: str | None) -> dict:
    return {
        "id": s.id,
        "device": sessions.device(s.user_agent),
        "ip": s.ip,
        "method": s.method,
        "created_at": s.created_at,
        "last_seen_at": s.last_seen_at,
        "current": s.id == current,
    }


@router.get("/sessions")
def my_sessions(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _session_only(p)
    return [_session_view(s, p.session_id) for s in sessions.active(db, p.user_id)]


@router.delete("/sessions/{session_id}", status_code=204)
def end_session(session_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _session_only(p)
    if not sessions.revoke(db, p.user_id, session_id):
        raise HTTPException(404, "Session not found")


@router.post("/logout", status_code=204)
def logout(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if p.session_id:
        sessions.revoke(db, p.user_id, p.session_id)


@router.post("/logout-others")
def logout_others(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Sign out everywhere except this device."""
    _session_only(p)
    return {"ended": sessions.revoke_others(db, p.user_id, p.session_id)}
