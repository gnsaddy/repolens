from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Organization, User
from ..schemas import LoginIn, RegisterIn, TokenOut, UserCreate, UserOut
from ..security import create_token, hash_password, verify_password
from ..services.policy import authorize

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = hash_password("dummy-password-for-timing")


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
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
    return TokenOut(access_token=create_token(user.id, org.id, user.role), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenOut(access_token=create_token(user.id, user.org_id, user.role), user=UserOut.model_validate(user))


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
