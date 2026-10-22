from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import DocProposal, Document, DriftAlert, Investigation, Repository
from ..schemas import ProposalDecision, ProposalEdit, RunbookIn
from ..security import redact_stored
from ..services import proposals as pr
from ..services.access import can_view_investigation, scope_for
from ..services.policy import authorize

router = APIRouter(prefix="/api/wiki/proposals", tags=["wiki-proposals"])


def apply_options(db: Session, p: DocProposal) -> list[str]:
    """Where an approval can write: the collection itself, a Confluence comment, or nowhere (code repository pages)."""
    if p.kind == "new":
        return ["local"]
    repo = db.get(Repository, p.doc_repo_id) if p.doc_repo_id else None
    if repo is None or repo.source != "docs":
        return []
    doc = db.scalar(select(Document).where(Document.repo_id == repo.id, Document.path == p.doc_path))
    if doc is None:
        return []
    if doc.source_type == "confluence":
        return ["confluence_comment"]
    return ["local"] if doc.source_type in ("upload", "url", "") else []


def _view(p: DocProposal, repo_name: str = "", full: bool = False, options: list[str] | None = None) -> dict:
    out = {
        "id": p.id,
        "kind": p.kind,
        "source": p.source,
        "source_ref": p.source_ref,
        "doc_repo_id": p.doc_repo_id,
        "doc_repo": repo_name,
        "doc_path": p.doc_path,
        "title": p.title,
        "rationale": p.rationale,
        "drafted_by": p.drafted_by,
        "status": p.status,
        "result": p.result,
        "created_at": p.created_at,
        "decided_at": p.decided_at,
    }
    if full:
        out |= {
            "original": p.original,
            "proposed": p.proposed,
            "diff": pr.unified_diff(p.original, p.proposed, p.doc_path or "page.md"),
            "apply_options": options or [],
        }
    return out


def _get(db: Session, p: Principal, proposal_id: str) -> DocProposal:
    row = db.get(DocProposal, proposal_id)
    if row is None or row.org_id != p.org_id or (row.doc_repo_id and not scope_for(db, p).can(row.doc_repo_id)):
        raise HTTPException(404, "Proposal not found")
    return row


@router.get("")
def list_proposals(
    status: str = Query("pending", pattern="^(pending|applied|rejected|failed|all)$"),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    scope = scope_for(db, p)
    q = select(DocProposal).where(DocProposal.org_id == p.org_id).order_by(DocProposal.created_at.desc())
    if status != "all":
        q = q.where(DocProposal.status == status)
    names = {r.id: r.name for r in db.scalars(select(Repository).where(Repository.org_id == p.org_id))}
    return [
        _view(x, names.get(x.doc_repo_id, ""))
        for x in db.scalars(q.limit(200))
        if not x.doc_repo_id or scope.can(x.doc_repo_id)
    ]


@router.get("/{proposal_id}")
def get_proposal(proposal_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    row = _get(db, p, proposal_id)
    repo = db.get(Repository, row.doc_repo_id) if row.doc_repo_id else None
    return _view(row, repo.name if repo else "", full=True, options=apply_options(db, row))


@router.post("/from-drift/{alert_id}", status_code=201)
def from_drift(alert_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = db.get(DriftAlert, alert_id)
    scope = scope_for(db, p)
    if a is None or a.org_id != p.org_id or not scope.can(a.repo_id) or not scope.can(a.doc_repo_id):
        raise HTTPException(404, "Alert not found")
    authorize(db, p, "wiki.propose", a.doc_path)
    existing = db.scalar(
        select(DocProposal).where(
            DocProposal.source == "drift", DocProposal.source_ref == a.id, DocProposal.status == "pending"
        )
    )
    if existing is not None:
        return _view(existing, full=True, options=apply_options(db, existing))
    try:
        row = pr.draft_update(db, p.org_id, p.user_id, a)
    except pr.ProposalError as e:
        raise HTTPException(409, str(e)) from None
    return _view(row, full=True, options=apply_options(db, row))


@router.post("/from-incident", status_code=201)
def from_incident(body: RunbookIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    inv = db.get(Investigation, body.investigation_id)
    scope = scope_for(db, p)
    if inv is None or inv.org_id != p.org_id or not can_view_investigation(scope, inv, p.user_id):
        raise HTTPException(404, "Investigation not found")
    if body.collection_id:
        repo = db.get(Repository, body.collection_id)
        if repo is None or repo.org_id != p.org_id or repo.source != "docs" or not scope.can(repo.id):
            raise HTTPException(404, "Collection not found")
    authorize(db, p, "wiki.propose", inv.title)
    existing = db.scalar(
        select(DocProposal).where(
            DocProposal.source == "incident", DocProposal.source_ref == inv.id, DocProposal.status == "pending"
        )
    )
    if existing is not None:
        return _view(existing, full=True, options=apply_options(db, existing))
    try:
        row = pr.draft_runbook(db, p.org_id, p.user_id, inv, body.collection_id)
    except pr.ProposalError as e:
        raise HTTPException(409, str(e)) from None
    return _view(row, full=True, options=apply_options(db, row))


@router.put("/{proposal_id}")
def edit_proposal(
    proposal_id: str, body: ProposalEdit, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    """Adjust the draft before deciding."""
    row = _get(db, p, proposal_id)
    authorize(db, p, "wiki.propose", row.doc_path, op="edit")
    if row.status != "pending":
        raise HTTPException(409, "This proposal was already decided")
    row.proposed, _ = redact_stored(body.proposed)
    row.drafted_by = "edited"
    db.commit()
    return _view(row, full=True, options=apply_options(db, row))


@router.post("/{proposal_id}/decision")
def decide(
    proposal_id: str, body: ProposalDecision, p: Principal = Depends(current_principal), db: Session = Depends(get_db)
):
    row = _get(db, p, proposal_id)
    comment = body.approve and row.kind == "update" and body.target == "confluence_comment"
    authorize(
        db, p, "wiki.publish" if comment else "wiki.decide", row.doc_path, approve=body.approve, apply_to=body.target
    )
    try:
        row = pr.decide(db, row, p.user_id, body.approve, body.target)
    except pr.ProposalError as e:
        raise HTTPException(409, str(e)) from None
    return _view(row, full=True, options=apply_options(db, row))
