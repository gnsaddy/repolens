"""Model routing: how much does it cost, and does it ever send a hard task to a model that is too weak?
Uses three virtual models with realistic relative prices; no model is called."""

from ...config import get_settings
from ...services.llm import LLMConfig
from ...services.llm_router import Route, Task, decide

PRICE = {"light": (0.1, 0.4), "standard": (0.5, 2.0), "strong": (3.0, 15.0)}  # USD per 1M tokens in / out
TIERS = ("light", "standard", "strong")
OUT_TOKENS = {"settings-test": 5, "librarian": 300, "investigator": 900, "doc-updater": 1200, "runbook-writer": 1200}

# (task, the weakest tier that should handle it)
CASES: list[tuple[Task, str]] = (
    [(Task("settings-test", prompt_chars=40), "light")] * 3
    + [
        (Task("librarian", prompt_chars=n, evidence=e), "light")
        for n, e in [(1200, 3), (2500, 4), (4000, 6), (6000, 8), (9000, 9), (3000, 5)]
    ]
    * 3
    + [(Task("investigator", prompt_chars=2500, evidence=3), "standard")] * 4
    + [(Task("investigator", prompt_chars=9000, evidence=7, repos=1), "standard")] * 3
    + [(Task("investigator", prompt_chars=40000, evidence=9, has_trace=True, repos=2, low_confidence=True), "strong")]
    * 5
    + [(Task("investigator", prompt_chars=30000, evidence=8, has_trace=True, low_confidence=True), "strong")] * 3
    + [(Task("doc-updater", prompt_chars=6000), "standard")] * 3
    + [(Task("runbook-writer", prompt_chars=5000, evidence=4), "standard")] * 3
)


def _routes() -> list[Route]:
    return [Route(LLMConfig("eval", t, "", "k", "org", *PRICE[t]), t) for t in TIERS]


def _cost(tier: str, task: Task) -> float:
    tin, tout = PRICE[tier]
    return (task.prompt_chars / 4 * tin + OUT_TOKENS.get(task.kind, 500) * tout) / 1e6


def run() -> dict:
    routes = _routes()
    out: dict = {"tasks": len(CASES)}
    base_strong = sum(_cost("strong", t) for t, _ in CASES)
    base_standard = sum(_cost("standard", t) for t, _ in CASES)
    for savings in (True, False):
        cost, under, over, hard, hard_under, demoted = 0.0, 0, 0, 0, 0, 0
        for task, need in CASES:
            tier = decide(routes, task, savings).tier
            cost += _cost(tier, task)
            under += TIERS.index(tier) < TIERS.index(need)
            over += TIERS.index(tier) > TIERS.index(need)
            hard += need == "strong"
            hard_under += need == "strong" and tier != "strong"  # the costly mistake: a hard task on a weaker model
            demoted += need == "standard" and tier == "light"  # savings mode choosing a cheaper model on purpose
        key = "savings" if savings else "normal"
        out[f"{key}_cost_vs_all_strong"] = round(cost / base_strong, 3)
        out[f"{key}_cost_vs_all_standard"] = round(cost / base_standard, 3)
        out[f"{key}_under_routed"] = round(under / len(CASES), 3)  # any task on a weaker model than the label asks for
        out[f"{key}_hard_under_routed"] = round(hard_under / hard, 3)
        out[f"{key}_demoted_to_light"] = round(demoted / len(CASES), 3)
        out[f"{key}_over_routed"] = round(over / len(CASES), 3)
    out["thresholds"] = {"hard_points": 2}
    return out


__all__ = ["run", "get_settings"]
