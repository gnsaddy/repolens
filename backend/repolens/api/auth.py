from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Organization, User, UserSession
from ..schemas import LoginIn, PasswordChange, ProfilePatch, RegisterIn, TokenOut, UserCreate, UserOut
from ..security import hash_password, verify_password
from ..services import sessions
from ..services.policy import authorize

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = hash_password("dummy-password-for-timing")


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    email = body.email.lower()
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
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
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


@router.patch("/me", response_model=UserOut)
def update_profile(body: ProfilePatch, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = db.get(User, p.user_id)
    assert user is not None
    user.name = body.name.strip()
    db.commit()
    return user


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Change your password. Every other session is signed out."""
    user = db.get(User, p.user_id)
    assert user is not None
    if user.auth_source != "password":
        raise HTTPException(400, "Your account signs in through single sign-on; there is no password to change")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
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
    return [_session_view(s, p.session_id) for s in sessions.active(db, p.user_id)]


@router.delete("/sessions/{session_id}", status_code=204)
def end_session(session_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if not sessions.revoke(db, p.user_id, session_id):
        raise HTTPException(404, "Session not found")


@router.post("/logout", status_code=204)
def logout(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if p.session_id:
        sessions.revoke(db, p.user_id, p.session_id)


@router.post("/logout-others")
def logout_others(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Sign out everywhere except this device."""
    return {"ended": sessions.revoke_others(db, p.user_id, p.session_id)}
