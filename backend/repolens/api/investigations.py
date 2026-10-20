import asyncio
import json
from itertools import islice

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, session_factory
from ..deps import Principal, current_principal
from ..models import Investigation, MemoryEntry, Repository
from ..schemas import CiLogIn, CiStartIn, InvestigationCreate, InvestigationOut, InvestigationSummary, ReviewIn
from ..services import ci_logs
from ..services.access import can_view_investigation, scope_for
from ..services.investigations import create_investigation, submit
from ..services.policy import authorize
from ..telemetry import INVESTIGATIONS

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def get_inv(db: Session, p: Principal, inv_id: str) -> Investigation:
    inv = db.get(Investigation, inv_id)
    if inv is None or inv.org_id != p.org_id or not can_view_investigation(scope_for(db, p), inv, p.user_id):
        raise HTTPException(404, "Investigation not found")  # restricted results look exactly like missing ones
    return inv


def validate_repo_ids(db: Session, p: Principal, repo_ids: list[str]) -> list[str]:
    if not repo_ids:
        return []
    found = set(db.scalars(select(Repository.id).where(Repository.org_id == p.org_id, Repository.id.in_(repo_ids))))
    if found != set(repo_ids) or not scope_for(db, p).can_all(repo_ids):
        raise HTTPException(404, "Repository not found")
    return repo_ids


@router.post("", response_model=InvestigationSummary, status_code=202)
def start(body: InvestigationCreate, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    authorize(db, p, "investigation.create")
    repo_ids = validate_repo_ids(db, p, body.repo_ids)
    inv = create_investigation(db, p.org_id, p.user_id, body.query, repo_ids, body.title)
    INVESTIGATIONS.labels("started").inc()
    submit(inv.id)
    db.refresh(inv)
    return inv


@router.post("/ci/parse")
def ci_parse(body: CiLogIn, p: Principal = Depends(current_principal)):
    """Preview the failures found in a CI log (JUnit XML, pytest, Jest, Go test or plain stack traces)."""
    try:
        fmt, failures = ci_logs.parse_ci_log(body.log)
    except ci_logs.CiLogError as e:
        raise HTTPException(400, str(e)) from None
    return {"format": fmt, "failures": [ci_logs.sanitize(f).as_dict(i) for i, f in enumerate(failures)]}


@router.post("/ci/start", status_code=202)
def ci_start(body: CiStartIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Start one investigation per selected failure. The log is parsed again on the server; indexes only select."""
    authorize(db, p, "investigation.create", "ci", count=len(body.indexes))
    repo_ids = validate_repo_ids(db, p, body.repo_ids)
    try:
        _, failures = ci_logs.parse_ci_log(body.log)
    except ci_logs.CiLogError as e:
        raise HTTPException(400, str(e)) from None
    chosen = sorted(set(body.indexes))
    if chosen[-1] >= len(failures) or chosen[0] < 0:
        raise HTTPException(400, "Selected failure is not in the log")
    started = []
    for i in chosen:
        f = ci_logs.sanitize(failures[i])
        inv = create_investigation(
            db, p.org_id, p.user_id, ci_logs.to_query(f), repo_ids, f"CI failure: {f.name}"[:300]
        )
        INVESTIGATIONS.labels("started").inc()
        submit(inv.id)
        started.append({"id": inv.id, "title": inv.title})
    return {"started": started}


@router.get("", response_model=list[InvestigationSummary])
def list_investigations(limit: int = 50, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    scope = scope_for(db, p)
    q = select(Investigation).where(Investigation.org_id == p.org_id).order_by(Investigation.created_at.desc())
    if not scope.restricted:
        return db.scalars(q.limit(min(limit, 200))).all()
    visible = (i for i in db.scalars(q.limit(1000)) if can_view_investigation(scope, i, p.user_id))
    return list(islice(visible, min(limit, 200)))


@router.get("/{inv_id}", response_model=InvestigationOut)
def detail(inv_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return get_inv(db, p, inv_id)


@router.delete("/{inv_id}", status_code=204)
def delete(inv_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    inv = get_inv(db, p, inv_id)
    authorize(db, p, "investigation.delete", inv.id)
    db.delete(inv)
    db.commit()


@router.post("/{inv_id}/review", response_model=InvestigationOut)
def review(inv_id: str, body: ReviewIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Human outcome: confirmed root causes become verified incident memory."""
    inv = get_inv(db, p, inv_id)
    authorize(db, p, "investigation.review", inv.id, outcome=body.outcome)
    if inv.status != "completed":
        raise HTTPException(409, "Investigation is not complete")
    inv.root_cause_status = body.outcome
    for m in db.scalars(select(MemoryEntry).where(MemoryEntry.source_id == inv.id)):
        m.verified = body.outcome == "confirmed"
        if body.outcome == "rejected":
            db.delete(m)
    db.commit()
    return inv


@router.get("/{inv_id}/events")
async def events(inv_id: str, p: Principal = Depends(current_principal)):
    """Server-sent events: streams timeline steps until the investigation finishes."""
    db0 = session_factory()()
    try:
        get_inv(db0, p, inv_id)
    finally:
        db0.close()

    async def gen():
        sent = 0
        for _ in range(600):
            db = session_factory()()
            try:
                inv = db.get(Investigation, inv_id)
                timeline, status = list(inv.timeline), inv.status
            finally:
                db.close()
            for step in timeline[sent:]:
                yield f"event: step\ndata: {json.dumps(step)}\n\n"
            sent = len(timeline)
            if status in ("completed", "failed"):
                yield f"event: done\ndata: {json.dumps({'status': status})}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")
