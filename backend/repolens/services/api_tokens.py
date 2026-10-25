"""Personal access tokens: long-lived credentials for scripts, CI jobs and editor tools, that a person can revoke."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApiToken, User, _now

PREFIX = "rlp_"
TOUCH_EVERY = timedelta(minutes=5)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(db: Session, user: User, name: str, scope: str, days: int | None) -> tuple[str, ApiToken]:
    token = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        org_id=user.org_id,
        user_id=user.id,
        name=name.strip()[:100],
        prefix=token[:8],
        token_hash=_hash(token),
        scope=scope,
        expires_at=_now() + timedelta(days=days) if days else None,
    )
    db.add(row)
    db.commit()
    return token, row


def authenticate(db: Session, token: str) -> tuple[ApiToken, User] | None:
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None:
        exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=_now().tzinfo)
        if exp < _now():
            return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    last = row.last_used_at
    if last is None or _now() - (last if last.tzinfo else last.replace(tzinfo=_now().tzinfo)) > TOUCH_EVERY:
        row.last_used_at = _now()
        db.commit()
    return row, user
