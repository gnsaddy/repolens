import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import AuditEvent, User
from ..security import redact_secrets
from ..services.policy import authorize

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _view(e: AuditEvent, who: dict[str, str]) -> dict:
    return {
        "id": e.id,
        "at": e.created_at,
        "actor": who.get(e.actor_id) or (e.actor_id[:8] if e.actor_id else "system"),
        "action": e.action,
        "target": redact_secrets(e.target or "")[:200],
        "allowed": e.allowed,
        "detail": {k: redact_secrets(str(v))[:200] for k, v in (e.detail or {}).items()},
    }


def _query(org_id: str, action: str, allowed: str, actor: str, q: str, since: datetime | None):
    stmt = select(AuditEvent).where(AuditEvent.org_id == org_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if allowed in ("yes", "no"):
        stmt = stmt.where(AuditEvent.allowed.is_(allowed == "yes"))
    if actor:
        stmt = stmt.where(AuditEvent.actor_id == actor)
    if q:
        stmt = stmt.where(AuditEvent.target.ilike(f"%{q}%") | AuditEvent.action.ilike(f"%{q}%"))
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)
    return stmt


@router.get("")
def list_events(
    action: str = Query("", max_length=100),
    allowed: str = Query("", pattern="^(yes|no|)$"),
    actor: str = Query("", max_length=40),
    q: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Who did what, and what was refused (admins). Newest first."""
    authorize(db, p, "llm.configure", "audit-log", op="read")
    stmt = _query(p.org_id, action, allowed, actor, q, None)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit)))
    who = {u.id: u.email for u in db.scalars(select(User).where(User.org_id == p.org_id))}
    return {"total": total, "items": [_view(e, who) for e in rows]}


@router.get("/facets")
def facets(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The action groups and people that appear in the log, for the filters."""
    authorize(db, p, "llm.configure", "audit-log", op="read")
    actions = db.scalars(select(AuditEvent.action).where(AuditEvent.org_id == p.org_id).distinct()).all()
    groups = sorted({a.split(".")[0] for a in actions})
    users = [{"id": u.id, "email": u.email} for u in db.scalars(select(User).where(User.org_id == p.org_id))]
    denied = (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.org_id == p.org_id, AuditEvent.allowed.is_(False))
        )
        or 0
    )
    return {"groups": groups, "users": users, "denied": denied}


@router.get("/export.csv")
def export_csv(
    action: str = Query("", max_length=100),
    allowed: str = Query("", pattern="^(yes|no|)$"),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """The same log as a CSV file (up to 5,000 rows, newest first)."""
    authorize(db, p, "llm.configure", "audit-log", op="export")
    rows = db.scalars(
        _query(p.org_id, action, allowed, "", "", None).order_by(AuditEvent.created_at.desc()).limit(5000)
    )
    who = {u.id: u.email for u in db.scalars(select(User).where(User.org_id == p.org_id))}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "actor", "action", "target", "allowed", "detail"])
    for e in rows:
        v = _view(e, who)
        # a leading = + - @ would be run as a formula by spreadsheet programs
        safe = lambda s: ("'" + s) if s[:1] in ("=", "+", "-", "@") else s  # noqa: E731
        w.writerow(
            [
                v["at"].astimezone(UTC).isoformat(),
                safe(v["actor"]),
                v["action"],
                safe(v["target"]),
                v["allowed"],
                safe(str(v["detail"])),
            ]
        )
    return Response(
        out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'}
    )
