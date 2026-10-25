"""Multi-provider, multi-model routing: pick the cheapest model that is good enough for each task.

Every enabled model belongs to a tier. Light models (small, cheap, fast) handle lookups and short answers; standard
models write the main analyses; strong models are kept for hard or high-stakes work. Each task is scored from signals
we already have (task kind, prompt size, amount of evidence, a stack trace, several repositories, weak retrieval) and
the score moves the task one tier up. With savings mode on, plain tasks are also moved one tier down. If the chosen
model fails (rate limit, outage, unknown model) the next one in the chain is tried, and a caller may escalate to a
stronger tier when the answer is unusable. With one model configured, routing is a no-op.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LLMConnection, LLMModel, LLMSetting
from ..security import decrypt_secret
from .llm import CHAT_PROVIDERS, GEMINI_URL, LLMConfig, resolve_config

TIERS = ("light", "standard", "strong")
# What each kind of task needs by default.
KIND_TIER = {
    "settings-test": "light",
    "librarian": "light",  # answering a question from retrieved sources
    "investigator": "standard",  # root-cause narrative
    "doc-updater": "standard",
    "runbook-writer": "standard",
    "fix-writer": "strong",  # writing code that a person will review: use the best model available
}
KIND_LABEL = {
    "settings-test": "Connection tests",
    "librarian": "Questions over code and docs",
    "investigator": "Root-cause analysis",
    "doc-updater": "Wiki page updates",
    "runbook-writer": "Runbook drafts",
    "fix-writer": "Suggested code fixes",
}


@dataclass
class Task:
    kind: str
    prompt_chars: int = 0
    evidence: int = 0
    has_trace: bool = False
    repos: int = 1
    low_confidence: bool = False
    prefer: str | None = None  # force a tier


@dataclass
class Route:
    cfg: LLMConfig
    tier: str
    model_row: str = ""  # registry id, "" for the legacy / environment configuration


@dataclass
class Decision:
    tier: str
    chain: list[Route]
    why: list[str] = field(default_factory=list)


def complexity(t: Task) -> tuple[int, list[str]]:
    """Points that say this task is harder than usual, with the reasons (shown to the user)."""
    pts = 0
    why: list[str] = []
    tokens = t.prompt_chars // 4
    if tokens > 8000:
        pts, why = pts + 2, [*why, f"large prompt (~{tokens} tokens)"]
    elif tokens > 3000:
        pts, why = pts + 1, [*why, f"long prompt (~{tokens} tokens)"]
    if t.evidence > 6:
        pts, why = pts + 1, [*why, f"{t.evidence} pieces of evidence"]
    if t.has_trace:
        pts, why = pts + 1, [*why, "stack trace"]
    if t.repos > 1:
        pts, why = pts + 1, [*why, f"{t.repos} repositories"]
    if t.low_confidence:
        pts, why = pts + 1, [*why, "weak retrieval"]
    return pts, why


def _price(r: Route) -> float:
    c = r.cfg
    return (
        (c.input_price or 0.0) + (c.output_price or 0.0)
        if c.input_price is not None or c.output_price is not None
        else 1e9
    )


def all_routes(db: Session, org_id: str) -> list[Route]:
    """Every usable model: the registry when the organisation has one, otherwise the single legacy/environment setup."""
    rows = db.execute(
        select(LLMModel, LLMConnection)
        .join(LLMConnection, LLMConnection.id == LLMModel.connection_id)
        .where(LLMModel.org_id == org_id, LLMModel.enabled.is_(True))
        .order_by(LLMModel.created_at)
    ).all()
    routes = []
    for m, c in rows:
        if c.provider not in CHAT_PROVIDERS or not c.api_key_encrypted:
            continue
        base = GEMINI_URL if c.provider == "gemini" else c.base_url or "https://api.openai.com/v1"
        routes.append(
            Route(
                LLMConfig(
                    c.provider, m.model, base, decrypt_secret(c.api_key_encrypted), "org", m.input_price, m.output_price
                ),
                m.tier,
                m.id,
            )
        )
    if routes:
        return routes
    if db.scalar(select(LLMModel.id).where(LLMModel.org_id == org_id).limit(1)):
        return []  # a registry exists but everything is disabled: stay offline rather than silently using the old setup
    cfg = resolve_config(db, org_id)
    return [Route(cfg, "standard")] if cfg.provider in CHAT_PROVIDERS else []


def savings_mode(db: Session, org_id: str) -> bool:
    row = db.scalar(select(LLMSetting).where(LLMSetting.org_id == org_id))
    return True if row is None else bool(row.savings_mode)


def decide(routes: list[Route], task: Task, savings: bool = True, budget_low: bool = False) -> Decision:
    """Choose the tier and the fallback chain for a task."""
    if not routes:
        return Decision("standard", [])
    base = TIERS.index(KIND_TIER.get(task.kind, "standard"))
    pts, why = complexity(task)
    idx = base
    if task.prefer in TIERS:
        idx, why = TIERS.index(task.prefer), [f"{task.prefer} tier requested"]
    else:
        if pts >= 2:
            idx = min(idx + 1, 2)
            why = [f"harder than usual: {', '.join(why)}" if why else "harder than usual"]
        elif savings and pts == 0 and idx > 0:
            idx -= 1
            why = ["plain task, so a cheaper model is enough"]
        elif not why:
            why = [f"{TIERS[idx]} task"]
    if budget_low and idx > 0:
        idx, why = 0, [*why, "budget nearly used, using the cheapest tier"]
    have = {t for t in TIERS if any(r.tier == t for r in routes)}
    if len(have) == 1:
        idx = TIERS.index(next(iter(have)))  # only one tier configured
    order = sorted(range(3), key=lambda i: (abs(i - idx), -i))  # the wanted tier, then nearest, preferring stronger
    chain: list[Route] = []
    for i in order:
        chain += sorted((r for r in routes if r.tier == TIERS[i]), key=_price)
    return Decision(chain[0].tier if chain else TIERS[idx], chain, why)


def stronger(chain: list[Route], current: Route) -> Route | None:
    """The next model up from `current` (for retrying an unusable answer), or None."""
    up = [r for r in chain if TIERS.index(r.tier) > TIERS.index(current.tier)]
    return min(up, key=lambda r: (TIERS.index(r.tier), _price(r))) if up else None


def plan(routes: list[Route], savings: bool) -> list[dict]:
    """What each kind of task would use right now, for the Settings page."""
    out = []
    for kind, label in KIND_LABEL.items():
        d = decide(routes, Task(kind), savings)
        first = d.chain[0] if d.chain else None
        out.append(
            {
                "kind": kind,
                "label": label,
                "tier": d.tier,
                "model": first.cfg.model if first else None,
                "provider": first.cfg.provider if first else None,
                "why": d.why[0] if d.why else "",
            }
        )
    return out
