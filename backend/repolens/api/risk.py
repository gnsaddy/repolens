from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, current_principal
from ..models import RiskAssessment
from ..schemas import PrReportIn, RiskAssessIn
from ..services import gitops
from ..services import pr_report as pr_report_svc
from ..services import risk as oracle
from ..services.access import Scope, scope_for
from ..services.policy import authorize
from .repos import _get_repo

router = APIRouter(prefix="/api/risk", tags=["risk"])


def _filter_affected(affected: dict, scope: Scope) -> dict:
    """Stored assessments may mention repositories the *viewer* cannot see; drop those entries on read."""
    if not scope.restricted:
        return affected
    out = dict(affected)
    for key in ("dependents", "tests_to_run", "docs_to_update"):
        out[key] = [d for d in affected.get(key, []) if scope.can(d.get("repo_id"))]
    return out


def _filter_signals(signals: list[dict], scope: Scope) -> list[dict]:
    if not scope.restricted:
        return signals
    return [
        {**s, "evidence": [e for e in s.get("evidence", []) if "repo_id" not in e or scope.can(e["repo_id"])]}
        for s in signals
    ]


def _view(r: RiskAssessment, scope: Scope | None = None) -> dict:
    return {
        "id": r.id,
        "repo_id": r.repo_id,
        "ref_type": r.ref_type,
        "ref": r.ref,
        "title": r.title,
        "score": r.score,
        "level": r.level,
        "model_version": r.model_version,
        "signals": _filter_signals(r.signals, scope) if scope else r.signals,
        "affected": _filter_affected(r.affected, scope) if scope else r.affected,
        "limitations": r.limitations,
        "created_at": r.created_at,
    }


def persist(db: Session, org_id: str, repo_id: str, user_id: str, result: dict) -> RiskAssessment:
    row = RiskAssessment(
        org_id=org_id,
        repo_id=repo_id,
        created_by=user_id,
        ref_type=result["ref_type"],
        ref=result["ref"][:300],
        title=result["title"][:500],
        score=result["score"],
        level=result["level"],
        model_version=result["model_version"],
        signals=result["signals"],
        affected=result["affected"],
        limitations=result["limitations"],
    )
    db.add(row)
    db.commit()
    return row


@router.post("/pr-report")
def pr_report(body: PrReportIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """A comment-ready review of a pull request from its changed files; nothing is stored. Used by the GitHub Action."""
    authorize(db, p, "risk.preview", body.repo)
    scope = scope_for(db, p)
    repo = pr_report_svc.find_repo(db, p.org_id, scope, body.repo)
    if repo is None:
        raise HTTPException(404, "No repository you can see matches that name. Add it to RepoLens first.")
    return pr_report_svc.build(
        db, repo, scope, [f.model_dump() for f in body.files], body.title, body.pr_number, body.app_url, body.full
    )


@router.post("/assess", status_code=201)
def assess(body: RiskAssessIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    repo = _get_repo(db, p, body.repo_id)
    modes = [body.sha is not None, body.pr_number is not None, bool(body.base or body.head), body.files is not None]
    if sum(modes) != 1:
        raise HTTPException(422, "Provide exactly one of: sha, pr_number, base+head, files")
    authorize(db, p, "risk.assess", repo.name)
    try:
        if body.sha:
            ci = oracle.from_commit(db, repo, body.sha)
        elif body.pr_number:
            ci = oracle.from_pr(db, repo, body.pr_number)
        elif body.base or body.head:
            if not (body.base and body.head):
                raise HTTPException(422, "Both base and head are required")
            ci = oracle.from_range(repo, body.base, body.head)
        else:
            ci = oracle.from_files(repo, [f.model_dump() for f in body.files or []], body.title)
        if body.title and ci.ref_type != "commit":
            ci.title = body.title
    except gitops.GitError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    scope = scope_for(db, p)
    return _view(persist(db, p.org_id, repo.id, p.user_id, oracle.assess(db, ci, scope)), scope)


@router.get("/scan")
def scan(
    repo_id: str,
    limit: int = Query(15, ge=1, le=40),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    """Rank the repository's most recent commits by risk (computed on the fly, not stored)."""
    repo = _get_repo(db, p, repo_id)
    return oracle.rank_recent_commits(db, repo, limit, scope_for(db, p))


@router.get("")
def list_assessments(
    repo_id: str | None = None,
    limit: int = Query(30, le=100),
    p: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    q = select(RiskAssessment).where(RiskAssessment.org_id == p.org_id)
    if repo_id:
        _get_repo(db, p, repo_id)
        q = q.where(RiskAssessment.repo_id == repo_id)
    visible = scope_for(db, p).ids_or_none()
    if visible is not None:
        q = q.where(RiskAssessment.repo_id.in_(visible))
    rows = db.scalars(q.order_by(RiskAssessment.created_at.desc()).limit(limit))
    return [
        {k: v for k, v in _view(r).items() if k not in ("signals", "affected", "limitations")}
        | {"top_signals": [s["label"] for s in sorted(r.signals, key=lambda s: -s["points"])[:2] if s["points"] > 0]}
        for r in rows
    ]


@router.get("/{assessment_id}")
def get_assessment(assessment_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    r = db.get(RiskAssessment, assessment_id)
    scope = scope_for(db, p)
    if r is None or r.org_id != p.org_id or not scope.can(r.repo_id):
        raise HTTPException(404, "Assessment not found")
    return _view(r, scope)
