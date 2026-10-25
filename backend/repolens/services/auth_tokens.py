"""One-time links for invitations and password resets. Only a hash of the token is stored."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AuthToken, User, _now

LIFETIME = {"invite": timedelta(days=7), "reset": timedelta(hours=24)}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db: Session, user: User, kind: str, created_by: str) -> tuple[str, AuthToken]:
    """A new link for this person; any earlier unused link for them stops working."""
    db.execute(
        update(AuthToken).where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None)).values(used_at=_now())
    )
    token = secrets.token_urlsafe(32)
    row = AuthToken(
        org_id=user.org_id,
        user_id=user.id,
        kind=kind,
        token_hash=_hash(token),
        created_by=created_by,
        expires_at=_now() + LIFETIME[kind],
    )
    db.add(row)
    db.commit()
    return token, row


def redeem(db: Session, token: str) -> tuple[AuthToken, User] | None:
    """The link's owner if it is valid (unused and not expired); the caller marks it used after the password is set."""
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == _hash(token.strip())))
    if row is None or row.used_at is not None:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=_now().tzinfo)
    if exp < _now():
        return None
    user = db.get(User, row.user_id)
    return (row, user) if user is not None and user.active else None


def pending_for(db: Session, org_id: str) -> dict[str, str]:
    """user id -> kind of the link still waiting to be used."""
    now = _now()
    return {
        t.user_id: t.kind
        for t in db.scalars(select(AuthToken).where(AuthToken.org_id == org_id, AuthToken.used_at.is_(None)))
        if (t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=now.tzinfo)) > now
    }
