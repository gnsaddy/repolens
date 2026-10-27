"""Token telemetry: every model call becomes an LLMCallEvent; costs use a versioned pricing config."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LLMCallEvent
from ..telemetry import LLM_TOKENS


class BudgetExceeded(Exception):
    pass


def load_pricing() -> dict[str, dict[str, float]]:
    raw = get_settings().pricing_json
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int, cached: int = 0, override: dict[str, float] | None = None
) -> tuple[float, str]:
    """Returns (cost_usd, pricing_version). Unpriced models cost 0 and are tagged 'unpriced'.
    `override` is the organisation's own price (USD per 1M tokens) set in the settings UI."""
    p = override or load_pricing().get(model)
    if not p:
        return 0.0, "unpriced"
    fresh = max(input_tokens - cached, 0)
    cost = (
        fresh * p.get("input", 0.0)
        + cached * p.get("cached_input", p.get("input", 0.0))
        + output_tokens * p.get("output", 0.0)
    ) / 1_000_000
    return round(cost, 6), ("org-settings" if override else get_settings().pricing_version)


def record_event(
    db: Session,
    org_id: str,
    *,
    agent: str,
    provider: str,
    model: str,
    kind: str = "chat",
    workflow: str = "rca",
    workflow_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    latency_ms: int = 0,
    cache_hit: bool = False,
    tokens_avoided: int = 0,
    usage_source: str = "provider_reported",
    success: bool = True,
    error: str | None = None,
    jira_issue_key: str | None = None,
    pricing_override: dict[str, float] | None = None,
) -> LLMCallEvent:
    cost, ver = (
        (0.0, "local")
        if usage_source == "local"
        else estimate_cost(model, input_tokens, output_tokens, cached_input_tokens, pricing_override)
    )
    ev = LLMCallEvent(
        org_id=org_id,
        workflow=workflow,
        workflow_id=workflow_id,
        agent_name=agent,
        kind=kind,
        provider=provider,
        model_name=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        tokens_avoided=tokens_avoided,
        estimated_cost=cost,
        pricing_version=ver,
        usage_source=usage_source,
        success=success,
        error=error,
        jira_issue_key=jira_issue_key,
    )
    db.add(ev)
    db.commit()
    for kind_, n in (("input", input_tokens), ("output", output_tokens), ("cached_input", cached_input_tokens)):
        if n and usage_source != "local":  # the offline embedder spends no tokens anyone pays for
            LLM_TOKENS.labels(kind_).inc(n)
    return ev


def spend_since(db: Session, org_id: str, since: datetime) -> float:
    return float(
        db.scalar(
            select(func.coalesce(func.sum(LLMCallEvent.estimated_cost), 0.0)).where(
                LLMCallEvent.org_id == org_id, LLMCallEvent.created_at >= since
            )
        )
        or 0.0
    )


def check_budget(db: Session, org_id: str) -> None:
    """Hard limit: raises BudgetExceeded so optional inference is skipped."""
    s = get_settings()
    now = datetime.now(UTC)
    if s.daily_budget_usd > 0 and spend_since(db, org_id, now - timedelta(days=1)) >= s.daily_budget_usd:
        raise BudgetExceeded("daily budget exceeded")
    if s.monthly_budget_usd > 0 and spend_since(db, org_id, now - timedelta(days=30)) >= s.monthly_budget_usd:
        raise BudgetExceeded("monthly budget exceeded")


def budget_low(db: Session, org_id: str, fraction: float = 0.9) -> bool:
    """True when 90% or more of a configured budget is already spent: a signal to prefer cheaper models."""
    s = get_settings()
    now = datetime.now(UTC)
    if s.daily_budget_usd > 0 and spend_since(db, org_id, now - timedelta(days=1)) >= fraction * s.daily_budget_usd:
        return True
    return (
        s.monthly_budget_usd > 0
        and spend_since(db, org_id, now - timedelta(days=30)) >= fraction * s.monthly_budget_usd
    )


def summarize(db: Session, org_id: str, days: int = 30) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    rows = db.scalars(
        select(LLMCallEvent).where(LLMCallEvent.org_id == org_id, LLMCallEvent.created_at >= prev_since)
    ).all()

    def _aware(d):
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    cur = [r for r in rows if _aware(r.created_at) >= since]
    prev = [r for r in rows if _aware(r.created_at) < since]

    def totals(rs):
        chat = [r for r in rs if r.kind == "chat"]
        return {
            "input_tokens": sum(r.input_tokens for r in rs),
            "output_tokens": sum(r.output_tokens for r in rs),
            "cost": round(sum(r.estimated_cost for r in rs), 6),
            "requests": len(rs),
            "errors": sum(1 for r in rs if not r.success),
            "cache_hits": sum(1 for r in chat if r.cache_hit),
            "chat_requests": len(chat),
            "tokens_avoided": sum(r.tokens_avoided for r in rs),
        }

    t, pt = totals(cur), totals(prev)
    t["cache_hit_rate"] = round(t["cache_hits"] / t["chat_requests"], 4) if t["chat_requests"] else 0.0
    t["error_rate"] = round(t["errors"] / t["requests"], 4) if t["requests"] else 0.0

    def group(key):
        agg: dict[str, dict] = {}
        for r in cur:
            k = key(r)
            a = agg.setdefault(
                k,
                {
                    "name": k,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "cost": 0.0,
                    "requests": 0,
                    "errors": 0,
                    "latency_ms_total": 0,
                },
            )
            a["input_tokens"] += r.input_tokens
            a["output_tokens"] += r.output_tokens
            a["cached_input_tokens"] += r.cached_input_tokens
            a["cost"] = round(a["cost"] + r.estimated_cost, 6)
            a["requests"] += 1
            a["errors"] += 0 if r.success else 1
            a["latency_ms_total"] += r.latency_ms
        out = []
        for a in agg.values():
            a["avg_latency_ms"] = int(a.pop("latency_ms_total") / a["requests"])
            out.append(a)
        return sorted(out, key=lambda a: a["input_tokens"] + a["output_tokens"], reverse=True)

    daily: dict[str, dict] = {}
    for r in cur:
        d = _aware(r.created_at).date().isoformat()
        e = daily.setdefault(d, {"date": d, "tokens": 0, "cost": 0.0})
        e["tokens"] += r.input_tokens + r.output_tokens
        e["cost"] = round(e["cost"] + r.estimated_cost, 6)

    from ..models import Investigation

    ok = (
        db.scalar(
            select(func.count())
            .select_from(Investigation)
            .where(
                Investigation.org_id == org_id,
                Investigation.status == "completed",
                Investigation.created_at >= since,
            )
        )
        or 0
    )
    t["cost_per_successful_rca"] = round(t["cost"] / ok, 6) if ok else None
    t["successful_investigations"] = ok

    def delta(cur_v, prev_v):
        return round((cur_v - prev_v) / prev_v, 4) if prev_v else None

    return {
        "days": days,
        "totals": t,
        "vs_previous": {
            "tokens": delta(t["input_tokens"] + t["output_tokens"], pt["input_tokens"] + pt["output_tokens"]),
            "cost": delta(t["cost"], pt["cost"]),
            "cache_hit_rate": None,
        },
        "by_model": group(lambda r: f"{r.provider}/{r.model_name}"),
        "by_agent": group(lambda r: r.agent_name or "-"),
        "by_workflow": group(lambda r: r.workflow),
        "daily": sorted(daily.values(), key=lambda d: d["date"]),
        "pricing_version": get_settings().pricing_version,
        "budgets": {"daily": get_settings().daily_budget_usd, "monthly": get_settings().monthly_budget_usd},
    }
