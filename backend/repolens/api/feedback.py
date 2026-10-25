from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import FeedbackEvent
from ..schemas import FeedbackIn
from ..security import redact_stored
from ..services.policy import authorize

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
def give_feedback(body: FeedbackIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Record a thumbs up or down. One verdict per person and answer: sending again replaces it."""
    authorize(db, p, "feedback.give", body.kind, rating=body.rating)
    row = None
    if body.target_id:
        row = db.scalar(
            select(FeedbackEvent).where(
                FeedbackEvent.org_id == p.org_id,
                FeedbackEvent.user_id == p.user_id,
                FeedbackEvent.kind == body.kind,
                FeedbackEvent.target_id == body.target_id,
            )
        )
    if row is None:
        row = FeedbackEvent(org_id=p.org_id, user_id=p.user_id, kind=body.kind, target_id=body.target_id)
        db.add(row)
    row.rating = body.rating
    row.query = redact_stored(body.query)[0][:2000]
    row.note = redact_stored(body.note)[0][:2000]
    row.meta = {k: v for k, v in body.meta.items() if isinstance(v, (str, int, float, bool))}
    db.commit()
    return {"id": row.id, "rating": row.rating}


@router.get("/summary")
def summary(
    days: int = Query(30, ge=1, le=365), p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """How helpful people found answers and investigations, overall and by kind."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        db.scalars(select(FeedbackEvent).where(FeedbackEvent.org_id == p.org_id, FeedbackEvent.created_at >= since))
    )
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        k = by.setdefault(r.kind, {"up": 0, "down": 0})
        k[r.rating] = k.get(r.rating, 0) + 1
    up = sum(v["up"] for v in by.values())
    total = sum(v["up"] + v["down"] for v in by.values())
    mem = [r for r in rows if r.meta.get("from_memory")]
    return {
        "days": days,
        "total": total,
        "helpful_rate": round(up / total, 3) if total else None,
        "by_kind": by,
        "from_memory": {
            "total": len(mem),
            "helpful_rate": round(sum(r.rating == "up" for r in mem) / len(mem), 3) if mem else None,
        },
    }


@router.get("/export")
def export(
    rating: str = Query("down", pattern="^(up|down)$"),
    limit: int = Query(200, ge=1, le=1000),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Questions people rated, for building evaluation sets (admins). Notes and queries are already redacted."""
    authorize(db, p, "llm.configure", "feedback-export", op="read")
    rows = db.scalars(
        select(FeedbackEvent)
        .where(FeedbackEvent.org_id == p.org_id, FeedbackEvent.rating == rating)
        .order_by(FeedbackEvent.created_at.desc())
        .limit(limit)
    )
    return [
        {"kind": r.kind, "query": r.query, "note": r.note, "meta": r.meta, "created_at": r.created_at} for r in rows
    ]
