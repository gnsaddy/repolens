from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import Investigation, LLMCallEvent, Repository
from ..services import usage
from ..services.access import can_view_investigation, scope_for

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/usage/summary")
def summary(
    days: int = Query(30, ge=1, le=365),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    return usage.summarize(db, p.org_id, days)


@router.get("/usage/events")
def events(limit: int = Query(50, le=500), p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(LLMCallEvent)
        .where(LLMCallEvent.org_id == p.org_id)
        .order_by(LLMCallEvent.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": e.id,
            "workflow": e.workflow,
            "workflow_id": e.workflow_id,
            "agent_name": e.agent_name,
            "kind": e.kind,
            "provider": e.provider,
            "model_name": e.model_name,
            "input_tokens": e.input_tokens,
            "output_tokens": e.output_tokens,
            "cached_input_tokens": e.cached_input_tokens,
            "latency_ms": e.latency_ms,
            "cache_hit": e.cache_hit,
            "estimated_cost": e.estimated_cost,
            "usage_source": e.usage_source,
            "pricing_version": e.pricing_version,
            "success": e.success,
            "jira_issue_key": e.jira_issue_key,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.get("/dashboard")
def dashboard(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    repos = [r for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id)) if scope.can(r.id)]
    all_inv = [
        i
        for i in db.scalars(
            select(Investigation).where(Investigation.org_id == p.org_id).order_by(Investigation.created_at.desc())
        )
        if can_view_investigation(scope, i, p.user_id)
    ]
    inv_total = len(all_inv)
    done = [i for i in all_inv if i.status == "completed"]
    reviewed = [i for i in done if i.root_cause_status in ("confirmed", "rejected")]
    confirmed = [i for i in reviewed if i.root_cause_status == "confirmed"]
    recent = all_inv[:8]
    names = {r.id: r.name for r in repos}
    top: dict[str, int] = {}
    for i in done:
        for rid in i.repo_ids or list(names):
            top[names.get(rid, rid)] = top.get(names.get(rid, rid), 0) + 1
    durations: list[float] = [float(i.evidence["duration_s"]) for i in done if i.evidence.get("duration_s") is not None]
    return {
        "repositories": len(repos),
        "indexed_repositories": sum(1 for r in repos if r.status == "ready"),
        "investigations": inv_total,
        "completed": len(done),
        "verified_accuracy": round(len(confirmed) / len(reviewed), 4) if reviewed else None,
        "reviewed": len(reviewed),
        "avg_duration_s": round(sum(durations) / len(durations), 2) if durations else None,
        "recent": [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "confidence": i.confidence,
                "created_at": i.created_at,
                "repo": names.get((i.repo_ids or [""])[0], "all"),
            }
            for i in recent
        ],
        "top_repositories": [
            {"name": k, "count": v} for k, v in sorted(top.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
    }
