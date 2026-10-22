from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import (
    AnswerMemory,
    CodeChunk,
    DocProposal,
    Document,
    DriftAlert,
    Investigation,
    KGEdge,
    KGNode,
    Repository,
    User,
    UserSession,
)
from ..services import answer_memory, usage
from ..services.access import scope_for

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


@router.get("/overview")
def overview(
    days: int = Query(30, ge=1, le=365), p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """How the workspace is being used: content, work done, AI spend and savings, and who is active."""
    org = p.org_id
    since = datetime.now(UTC) - timedelta(days=days)
    scope = scope_for(db, p)
    repos = [r for r in db.scalars(select(Repository).where(Repository.org_id == org)) if scope.can(r.id)]
    ids = [r.id for r in repos] or ["-"]
    inv = list(db.scalars(select(Investigation).where(Investigation.org_id == org, Investigation.created_at >= since)))
    done = [i for i in inv if i.status == "completed"]
    durations = [
        (i.finished_at - i.created_at).total_seconds()
        for i in done
        if i.finished_at is not None and i.finished_at.tzinfo == i.created_at.tzinfo
    ]
    u = usage.summarize(db, org, days)
    t = u["totals"] if "totals" in u else u
    used = t.get("input_tokens", 0) + t.get("output_tokens", 0)
    avoided = t.get("tokens_avoided", 0)
    mem = answer_memory.stats(db, org)
    active_users = _count(db, UserSession, UserSession.org_id == org, UserSession.last_seen_at >= since)
    return {
        "days": days,
        "content": {
            "repositories": sum(1 for r in repos if r.source != "docs"),
            "doc_collections": sum(1 for r in repos if r.source == "docs"),
            "pages": _count(db, Document, Document.org_id == org, Document.repo_id.in_(ids)),
            "chunks": _count(db, CodeChunk, CodeChunk.repo_id.in_(ids)),
            "graph_nodes": _count(db, KGNode, KGNode.org_id == org, KGNode.repo_id.in_(ids)),
            "graph_edges": _count(db, KGEdge, KGEdge.org_id == org, KGEdge.repo_id.in_(ids)),
            "indexing_errors": sum(1 for r in repos if r.status == "error"),
        },
        "work": {
            "investigations": len(inv),
            "completed": len(done),
            "failed": sum(1 for i in inv if i.status == "failed"),
            "confirmed": sum(1 for i in inv if i.root_cause_status == "confirmed"),
            "avg_minutes": round(sum(durations) / len(durations) / 60, 1) if durations else None,
            "proposals_pending": _count(db, DocProposal, DocProposal.org_id == org, DocProposal.status == "pending"),
            "proposals_applied": _count(db, DocProposal, DocProposal.org_id == org, DocProposal.status == "applied"),
            "drift_open": _count(db, DriftAlert, DriftAlert.org_id == org, DriftAlert.status == "open"),
        },
        "ai": {
            "requests": t.get("requests", 0),
            "tokens": used,
            "cost": t.get("cost", 0),
            "errors": t.get("errors", 0),
            "cache_hits": t.get("cache_hits", 0),
            "tokens_avoided": avoided,
            "savings_rate": round(avoided / (used + avoided), 3) if used + avoided else 0,
            "by_model": u.get("by_model", []),
            "by_agent": u.get("by_agent", []),
            "daily": u.get("daily", []),
        },
        "memory": {**mem, "stored": _count(db, AnswerMemory, AnswerMemory.org_id == org)},
        "team": {
            "users": _count(db, User, User.org_id == org, User.active.is_(True)),
            "active_sessions": _count(db, UserSession, UserSession.org_id == org, UserSession.revoked_at.is_(None)),
            "active_users_period": active_users,
        },
    }
