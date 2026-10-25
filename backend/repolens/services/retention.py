"""Remove records nobody needs any more, so the database stays small and old personal data does not linger.

Runs daily in the background and on demand (admins). Every rule is a setting; 0 keeps that kind of record forever."""

from datetime import timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AnswerMemory,
    AuditEvent,
    AuthToken,
    CacheEntry,
    FeedbackEvent,
    Investigation,
    UserSession,
    _now,
)


def sweep(db: Session, org_id: str | None = None) -> dict[str, int]:
    """Delete expired records (for one workspace, or all). Returns how many of each were removed."""
    s = get_settings()
    now = _now()
    out: dict[str, int] = {}

    def scoped(stmt, col):
        return stmt.where(col == org_id) if org_id else stmt

    def run(name: str, stmt) -> None:
        out[name] = getattr(db.execute(stmt), "rowcount", 0) or 0

    run(  # remembered answers past their lifetime are never reused, so they are only taking space
        "answer_memory",
        scoped(
            delete(AnswerMemory).where(AnswerMemory.created_at < now - timedelta(hours=s.answer_memory_ttl_hours)),
            AnswerMemory.org_id,
        ),
    )
    run(
        "cache_entries",
        scoped(
            delete(CacheEntry).where(CacheEntry.expires_at.is_not(None), CacheEntry.expires_at < now), CacheEntry.org_id
        ),
    )
    if s.retention_feedback_days:
        run(
            "feedback",
            scoped(
                delete(FeedbackEvent).where(FeedbackEvent.created_at < now - timedelta(days=s.retention_feedback_days)),
                FeedbackEvent.org_id,
            ),
        )
    if s.retention_audit_days:
        run(
            "audit",
            scoped(
                delete(AuditEvent).where(AuditEvent.created_at < now - timedelta(days=s.retention_audit_days)),
                AuditEvent.org_id,
            ),
        )
    if s.retention_sessions_days:
        cut = now - timedelta(days=s.retention_sessions_days)
        run(
            "sessions",
            scoped(
                delete(UserSession).where(or_(UserSession.revoked_at < cut, UserSession.last_seen_at < cut)),
                UserSession.org_id,
            ),
        )
        run(
            "links",
            scoped(delete(AuthToken).where(or_(AuthToken.used_at < cut, AuthToken.expires_at < cut)), AuthToken.org_id),
        )
    if s.retention_investigation_days:
        cut = now - timedelta(days=s.retention_investigation_days)
        ids = list(
            db.scalars(scoped(select(Investigation.id).where(Investigation.created_at < cut), Investigation.org_id))
        )
        for inv in db.scalars(select(Investigation).where(Investigation.id.in_(ids or ["-"]))):
            db.delete(inv)
        out["investigations"] = len(ids)
    db.commit()
    return out


def policy() -> dict:
    s = get_settings()
    return {
        "answer_memory_hours": s.answer_memory_ttl_hours,
        "feedback_days": s.retention_feedback_days,
        "audit_days": s.retention_audit_days,
        "sessions_days": s.retention_sessions_days,
        "investigation_days": s.retention_investigation_days,
    }
