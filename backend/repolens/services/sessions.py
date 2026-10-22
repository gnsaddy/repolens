"""Server-side sessions behind the signed tokens, so people can see where they are signed in and end a session."""

from datetime import timedelta

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import User, UserSession, _now
from ..security import create_token

TOUCH_EVERY = timedelta(seconds=60)


def start(db: Session, user: User, request: Request | None, method: str = "password") -> str:
    """Record a sign-in and return its token."""
    ip = (request.client.host if request and request.client else "")[:64]
    ua = (request.headers.get("user-agent", "") if request else "")[:300]
    row = UserSession(user_id=user.id, org_id=user.org_id, method=method, ip=ip, user_agent=ua)
    db.add(row)
    db.commit()
    return create_token(user.id, user.org_id, user.role, row.id)


def check(db: Session, sid: str, user_id: str) -> bool:
    """True while the session exists and has not been ended. Updates last-seen at most once a minute."""
    row = db.get(UserSession, sid)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    seen = row.last_seen_at if row.last_seen_at.tzinfo else row.last_seen_at.replace(tzinfo=_now().tzinfo)
    if _now() - seen > TOUCH_EVERY:
        row.last_seen_at = _now()
        db.commit()
    return True


def revoke(db: Session, user_id: str, sid: str) -> bool:
    row = db.get(UserSession, sid)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    db.commit()
    return True


def revoke_others(db: Session, user_id: str, keep: str | None) -> int:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if keep:
        q = q.where(UserSession.id != keep)
    n = getattr(db.execute(q.values(revoked_at=_now())), "rowcount", 0)
    db.commit()
    return n or 0


def active(db: Session, user_id: str) -> list[UserSession]:
    return list(
        db.scalars(
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .order_by(UserSession.last_seen_at.desc())
            .limit(50)
        )
    )


def device(ua: str) -> str:
    """A short, readable description of a browser from its user agent."""
    u = ua.lower()
    browser = next(
        (
            n
            for k, n in (
                ("edg/", "Edge"),
                ("chrome/", "Chrome"),
                ("firefox/", "Firefox"),
                ("safari/", "Safari"),
                ("curl", "curl"),
            )
            if k in u
        ),
        "Browser",
    )
    system = next(
        (
            n
            for k, n in (
                ("windows", "Windows"),
                ("mac os", "macOS"),
                ("android", "Android"),
                ("iphone", "iOS"),
                ("linux", "Linux"),
            )
            if k in u
        ),
        "",
    )
    return f"{browser} on {system}" if system else browser
