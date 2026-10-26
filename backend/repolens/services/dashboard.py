"""What the dashboard shows beyond the headline numbers: what needs attention, trends, AI savings and recent activity."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AuditEvent,
    ChatThread,
    CodeChunk,
    DocProposal,
    Document,
    DriftAlert,
    EvalRun,
    FeedbackEvent,
    Investigation,
    KGEdge,
    KGNode,
    Repository,
    User,
)
from . import usage, wiki
from .access import Scope, can_view_investigation

VERB = {  # audit action -> how it reads in the activity feed
    "repo.index": "indexed a repository",
    "repo.create": "added a repository",
    "repo.delete": "removed a repository",
    "investigation.create": "started an investigation",
    "investigation.review": "reviewed an investigation",
    "collection.create": "created a documentation collection",
    "document.write": "added or edited documentation",
    "wiki.propose": "proposed a wiki edit",
    "wiki.decide": "decided on a wiki edit",
    "jira.publish": "published a report to Jira",
    "llm.configure": "changed model settings",
    "user.create": "managed people",
    "auth.password_set": "set a password",
    "auth.mfa_enabled": "turned on an authenticator app",
    "auth.mfa_disabled": "turned off an authenticator app",
}
_QUIET = {"llm.test", "feedback.give"}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def build(db: Session, org: str, user_id: str, role: str, scope: Scope) -> dict:
    now = datetime.now(UTC)
    repos = [r for r in db.scalars(select(Repository).where(Repository.org_id == org)) if scope.can(r.id)]
    rids = [r.id for r in repos] or ["-"]
    inv = [
        i
        for i in db.scalars(select(Investigation).where(Investigation.org_id == org))
        if can_view_investigation(scope, i, user_id)
    ]

    # ---- needs attention
    week = now - timedelta(days=7)
    failed = [i for i in inv if i.status == "failed" and _aware(i.created_at) >= week]
    unreviewed = [i for i in inv if i.status == "completed" and i.root_cause_status == "unconfirmed"]
    shaky = [i for i in inv if i.status == "completed" and (i.confidence or 0) < 0.4 and _aware(i.created_at) >= week]
    broken = [r for r in repos if r.status == "error"]
    pending_repos = [r for r in repos if r.status in ("pending", "indexing")]
    drift = (
        db.scalar(
            select(func.count()).select_from(DriftAlert).where(DriftAlert.org_id == org, DriftAlert.status == "open")
        )
        or 0
    )
    proposals = (
        db.scalar(
            select(func.count())
            .select_from(DocProposal)
            .where(DocProposal.org_id == org, DocProposal.status == "pending")
        )
        or 0
    )
    attention: list[dict] = []

    def add(severity: str, text: str, to: str) -> None:
        attention.append({"severity": severity, "text": text, "to": to})

    if broken:
        add(
            "high",
            f"{len(broken)} repositor{'y' if len(broken) == 1 else 'ies'} failed to index: {', '.join(r.name for r in broken[:3])}",
            "/repositories",
        )
    if failed:
        add(
            "high",
            f"{len(failed)} investigation{'s' if len(failed) != 1 else ''} failed this week and can be run again",
            f"/investigations/{failed[0].id}",
        )
    if drift:
        add(
            "medium",
            f"{drift} wiki page{'s' if drift != 1 else ''} may be out of date because the code they describe changed",
            "/wiki?tab=drift",
        )
    if proposals:
        add(
            "medium",
            f"{proposals} suggested wiki edit{'s' if proposals != 1 else ''} waiting for your review",
            "/wiki?tab=proposals",
        )
    if unreviewed:
        add(
            "low",
            f"{len(unreviewed)} finished investigation{'s' if len(unreviewed) != 1 else ''} not yet confirmed or rejected (reviews make answers more trustworthy)",
            f"/investigations/{unreviewed[0].id}",
        )
    if shaky:
        add(
            "low",
            f"{len(shaky)} recent investigation{'s' if len(shaky) != 1 else ''} with low confidence; more documentation or a clearer report may help",
            f"/investigations/{shaky[0].id}",
        )
    if pending_repos:
        add(
            "info",
            f"{len(pending_repos)} repositor{'y is' if len(pending_repos) == 1 else 'ies are'} still being indexed",
            "/repositories",
        )

    # ---- investigations per day, last 14 days
    days = [(now - timedelta(days=d)).date() for d in range(13, -1, -1)]
    trend: dict = {d: {"date": d.isoformat(), "completed": 0, "failed": 0} for d in days}
    for i in inv:
        d = _aware(i.created_at).date()
        if d in trend and i.status in ("completed", "failed"):
            trend[d][i.status] += 1

    # ---- AI use and savings (30 days) and how helpful answers were
    u = usage.summarize(db, org, 30)
    t: dict = u["totals"]
    used, avoided = t["input_tokens"] + t["output_tokens"], t["tokens_avoided"]
    fb = list(
        db.scalars(
            select(FeedbackEvent).where(
                FeedbackEvent.org_id == org, FeedbackEvent.created_at >= now - timedelta(days=30)
            )
        )
    )
    ai = {
        "tokens": used,
        "avoided": avoided,
        "savings_rate": round(avoided / (used + avoided), 3) if used + avoided else 0,
        "cost": t["cost"],
        "requests": t["requests"],
        "helpful_rate": round(sum(f.rating == "up" for f in fb) / len(fb), 3) if fb else None,
        "ratings": len(fb),
        "daily": [{"date": d["date"], "tokens": d["tokens"]} for d in u["daily"][-14:]],
    }

    # ---- knowledge base snapshot
    health: dict = wiki.health(db, org, scope, limit=0)["summary"]
    knowledge = {
        "pages": db.scalar(
            select(func.count()).select_from(Document).where(Document.org_id == org, Document.repo_id.in_(rids))
        )
        or 0,
        "chunks": db.scalar(select(func.count()).select_from(CodeChunk).where(CodeChunk.repo_id.in_(rids))) or 0,
        "graph_nodes": db.scalar(
            select(func.count()).select_from(KGNode).where(KGNode.org_id == org, KGNode.repo_id.in_(rids))
        )
        or 0,
        "graph_edges": db.scalar(
            select(func.count()).select_from(KGEdge).where(KGEdge.org_id == org, KGEdge.repo_id.in_(rids))
        )
        or 0,
        "documented_ratio": health.get("documented_code_ratio"),
        "wiki_issues": sum(
            health[k] for k in ("orphans", "broken_links", "stale", "duplicates", "outdated_references")
        ),
        "drift_open": drift,
        "proposals_pending": proposals,
    }

    # ---- recent activity (admins read the workspace's audit trail)
    activity: list[dict] = []
    if role == "admin":
        who = {x.id: (x.name or x.email) for x in db.scalars(select(User).where(User.org_id == org))}
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.org_id == org, AuditEvent.allowed.is_(True))
            .order_by(AuditEvent.created_at.desc())
            .limit(80)
        )
        for e in rows:
            if e.action in _QUIET or e.action not in VERB:
                continue
            activity.append(
                {
                    "at": e.created_at,
                    "who": who.get(e.actor_id, "someone"),
                    "what": VERB[e.action],
                    "target": (e.target or "")[:80],
                }
            )
            if len(activity) >= 8:
                break

    first_steps = {  # drives the getting-started guide; counts only, never anyone's content
        "asked": bool(db.scalar(select(func.count()).select_from(ChatThread).where(ChatThread.user_id == user_id))),
        "measured": bool(
            db.scalar(
                select(func.count()).select_from(EvalRun).where(EvalRun.org_id == org, EvalRun.status == "completed")
            )
        ),
    }
    return {
        "attention": attention,
        "first_steps": first_steps,
        "investigations_per_day": list(trend.values()),
        "ai": ai,
        "knowledge": knowledge,
        "activity": activity,
        "week": {
            "started": sum(1 for i in inv if _aware(i.created_at) >= week),
            "failed": len(failed),
            "confirmed": sum(1 for i in inv if i.root_cause_status == "confirmed" and _aware(i.created_at) >= week),
        },
    }
