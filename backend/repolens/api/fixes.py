from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import FixProposal, Investigation, Repository
from ..schemas import FixDecisionIn
from ..services import fixes
from ..services.access import scope_for
from ..services.github import GitHubError
from ..services.policy import authorize
from .github import make_client, resolve_gh_repo
from .investigations import get_inv

router = APIRouter(tags=["fixes"])


def _view(f: FixProposal) -> dict:
    return {
        "id": f.id,
        "investigation_id": f.investigation_id,
        "repo_id": f.repo_id,
        "title": f.title,
        "rationale": f.rationale,
        "files": sorted({e["path"] for e in f.edits}),
        "diff": f.diff,
        "checks": f.checks,
        "tests": f.tests,
        "drafted_by": f.drafted_by,
        "model": f.model,
        "status": f.status,
        "result": f.result,
        "pr_url": f.pr_url,
        "created_at": f.created_at,
        "decided_at": f.decided_at,
    }


def _get(db: Session, p: Principal, fix_id: str) -> FixProposal:
    f = db.get(FixProposal, fix_id)
    if f is None or f.org_id != p.org_id:
        raise HTTPException(404, "Fix not found")
    get_inv(db, p, f.investigation_id)  # whoever cannot see the investigation cannot see its fixes
    return f


@router.post("/api/investigations/{inv_id}/fix-proposals", status_code=201)
def propose(inv_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    inv = get_inv(db, p, inv_id)
    authorize(db, p, "fix.propose", inv.id)
    try:
        return _view(fixes.propose(db, p.user_id, inv))
    except fixes.FixError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/api/investigations/{inv_id}/fix-proposals")
def for_investigation(inv_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    inv = get_inv(db, p, inv_id)
    rows = db.scalars(
        select(FixProposal).where(FixProposal.investigation_id == inv.id).order_by(FixProposal.created_at.desc())
    )
    return [_view(f) for f in rows]


@router.get("/api/fix-proposals/{fix_id}")
def read(fix_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return _view(_get(db, p, fix_id))


@router.get("/api/fix-proposals/{fix_id}/patch")
def patch(fix_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = _get(db, p, fix_id)
    return Response(
        f.diff,
        media_type="text/x-diff",
        headers={
            "Content-Disposition": f'attachment; filename="repolens-fix-{f.id[:8]}.patch"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/fix-proposals/{fix_id}/decision")
def decide(fix_id: str, body: FixDecisionIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Reject a fix, or approve it and open a draft pull request. Nothing is merged; a person reviews the pull request."""
    f = _get(db, p, fix_id)
    authorize(db, p, "fix.decide", f.id, decision=body.decision)
    if f.status != "pending":
        raise HTTPException(409, f"This fix was already {f.status}")
    f.decided_at = datetime.now(UTC)
    if body.decision == "reject":
        f.status = "rejected"
        db.commit()
        return _view(f)
    repo = db.get(Repository, f.repo_id)
    if repo is None or not scope_for(db, p).can(repo.id):
        raise HTTPException(404, "Repository not found")
    gh_repo = resolve_gh_repo(repo, None)
    if not gh_repo:
        raise HTTPException(400, "This repository is not on GitHub. Download the patch and apply it yourself.")
    inv = db.get(Investigation, f.investigation_id)
    try:
        f.pr_url = fixes.open_pull_request(db, f, make_client(db, p.org_id), gh_repo, inv.title if inv else "")
        f.status, f.result = "approved", "Draft pull request opened"
    except (fixes.FixError, GitHubError) as e:
        f.status, f.result = "failed", str(e)[:500]
        db.commit()
        raise HTTPException(409 if isinstance(e, fixes.FixError) else 502, f.result) from None
    db.commit()
    return _view(f)
