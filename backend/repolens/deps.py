from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuditEvent, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: str
    org_id: str
    role: str
    email: str = ""


def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        data = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None
    user = db.get(User, data["sub"])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    # Role comes from the database so demotions take effect immediately.
    return Principal(user.id, user.org_id, user.role, user.email)


_RANK = {"viewer": 0, "member": 1, "admin": 2}


def require_role(minimum: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if _RANK.get(p.role, -1) < _RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum} role")
        return p

    return dep


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
