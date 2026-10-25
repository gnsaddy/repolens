from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuditEvent, User
from .security import decode_token
from .services import api_tokens, sessions

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: str
    org_id: str
    role: str
    email: str = ""
    session_id: str | None = None
    token_scope: str = ""  # "read" | "write" when the caller used a personal access token


def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds.credentials.startswith(api_tokens.PREFIX):  # a personal access token, not a sign-in
        found = api_tokens.authenticate(db, creds.credentials)
        if found is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, expired or revoked access token")
        tok, tuser = found
        role = "viewer" if tok.scope == "read" else ("member" if tuser.role == "admin" else tuser.role)
        return Principal(tuser.id, tuser.org_id, role, tuser.email, token_scope=tok.scope)
    try:
        data = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    user = db.get(User, data["sub"])
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    sid = data.get("sid")
    if sid and not sessions.check(db, sid, user.id):  # signed out or revoked from another device
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This session was ended. Sign in again.")
    # Role comes from the database so demotions take effect immediately.
    return Principal(user.id, user.org_id, user.role, user.email, session_id=sid)


_RANK = {"viewer": 0, "member": 1, "admin": 2}


def require_role(minimum: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if _RANK.get(p.role, -1) < _RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum} role")
        return p

    return dep


def audit_user(db: Session, user: User, action: str, allowed: bool = True, **detail) -> None:
    """Record something that happened to or by a person who is not signed in (a failed code, a link being used)."""
    db.add(
        AuditEvent(
            org_id=user.org_id, actor_id=user.id, action=action, target=user.email[:200], allowed=allowed, detail=detail
        )
    )
    db.commit()


def audit(db: Session, p: Principal | None, action: str, target: str = "", allowed: bool = True, **detail):
    db.add(
        AuditEvent(
            org_id=p.org_id if p else "",
            actor_id=p.user_id if p else "",
            action=action,
            target=target,
            allowed=allowed,
            detail=detail,
        )
    )
    db.commit()
