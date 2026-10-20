"""Investigation lifecycle: create, run (background), persist, and record memory."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..agents.workflow import Ctx, Features, run_workflow
from ..config import get_settings
from ..db import session_factory
from ..models import Hypothesis, Investigation, MemoryEntry
from .access import repo_ids_in_evidence, scope_for_user_id
from .embeddings import Embedder
from .llm import LLMClient

log = logging.getLogger("repolens.investigations")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rca")


def create_investigation(
    db: Session,
    org_id: str,
    user_id: str,
    query: str,
    repo_ids: list[str],
    title: str | None = None,
    jira_issue_key: str | None = None,
) -> Investigation:
    inv = Investigation(
        org_id=org_id,
        created_by=user_id,
        query=query,
        repo_ids=repo_ids,
        title=(title or query.strip().splitlines()[0])[:200],
        jira_issue_key=jira_issue_key,
        timeline=[],
    )
    db.add(inv)
    db.commit()
    return inv


def submit(inv_id: str) -> None:
    if get_settings().run_jobs_inline:
        run_investigation(inv_id)
    else:
        _pool.submit(run_investigation, inv_id)


def run_investigation(inv_id: str, features: Features | None = None) -> None:
    db = session_factory()()
    try:
        inv = db.get(Investigation, inv_id)
        if inv is None:
            return
        inv.status = "running"
        db.commit()
        started = datetime.now(UTC)
        # The run is confined to what its creator may see *now* (a revoked grant takes effect immediately).
        scope = scope_for_user_id(db, inv.created_by)
        repo_ids = [r for r in inv.repo_ids if scope.can(r)] if inv.repo_ids else scope.resolve(None)
        if inv.repo_ids and not repo_ids:
            raise PermissionError("none of the selected repositories are accessible")

        def on_step(node: str, agent: str, summary: str, ms: int) -> None:
            fresh = db.get(Investigation, inv_id)
            fresh.timeline = [
                *fresh.timeline,
                {
                    "step": node,
                    "agent": agent,
                    "summary": summary,
                    "duration_ms": ms,
                    "at": datetime.now(UTC).isoformat(),
                },
            ]
            db.commit()

        ctx = Ctx(
            db=db,
            org_id=inv.org_id,
            investigation_id=inv.id,
            llm=LLMClient(db, inv.org_id, "rca", inv.id, inv.jira_issue_key),
            embedder=Embedder(),
            on_step=on_step,
            jira_issue_key=inv.jira_issue_key,
            features=features or Features(),
            scope=scope,
        )
        state = run_workflow(ctx, inv.query, repo_ids)
        inv = db.get(Investigation, inv_id)
        hyps = state.get("hypotheses", [])
        for h in hyps:
            inv.hypotheses.append(
                Hypothesis(
                    rank=h["rank"],
                    title=h["title"],
                    explanation=h["explanation"],
                    confidence=h["confidence"],
                    status=h["status"],
                    evidence=h["evidence"],
                )
            )
        narrative = state.get("narrative", {})
        inv.confidence = hyps[0]["confidence"] if hyps else 0.0
        inv.root_cause = narrative.get("root_cause", "")
        inv.suggested_fix = narrative.get("suggested_fix", "")
        inv.report_md = state.get("report_md", "")
        inv.verification = state.get("verification", {})
        inv.cache_hit = bool(state.get("cache_hit"))
        inv.evidence = {
            "plan": state.get("plan", []),
            "memory": state.get("memory", []),
            "callers": state.get("callers", []),
            "llm_used": state.get("llm_used", False),
            "duration_s": round((datetime.now(UTC) - started).total_seconds(), 2),
        }
        inv.status = "completed"
        inv.finished_at = datetime.now(UTC)
        db.commit()
        if (features or Features()).memory:
            _remember(db, inv)
    except Exception as e:
        log.exception("investigation %s failed", inv_id)
        db.rollback()
        inv = db.get(Investigation, inv_id)
        if inv:
            inv.status = "failed"
            inv.error = f"{type(e).__name__}: {e}"[:1000]
            inv.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


def _remember(db: Session, inv: Investigation) -> None:
    """Persist a reusable incident summary (unverified until a human confirms it)."""
    if not inv.root_cause:
        return
    summary = f"{inv.title}\nRoot cause (unconfirmed): {inv.root_cause}\nFix: {inv.suggested_fix}"
    emb = Embedder().embed([summary])[0]
    db.add(
        MemoryEntry(
            org_id=inv.org_id,
            kind="incident",
            source_id=inv.id,
            title=inv.title[:300],
            summary=summary,
            verified=False,
            scope_repo_ids=sorted(repo_ids_in_evidence(inv)),  # who may recall this later
            embedding=emb,
        )
    )
    db.commit()
