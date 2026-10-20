"""Pure metric helpers (no I/O) so they can be unit-tested."""

import math
from collections.abc import Sequence


def first_rank(ranks: Sequence[bool]) -> int | None:
    """1-based position of the first hit in a boolean hit list, or None."""
    for i, hit in enumerate(ranks, 1):
        if hit:
            return i
    return None


def recall_at_k(first_ranks: Sequence[int | None], k: int) -> float:
    return sum(1 for r in first_ranks if r is not None and r <= k) / len(first_ranks) if first_ranks else 0.0


def mrr(first_ranks: Sequence[int | None]) -> float:
    return sum(1.0 / r for r in first_ranks if r) / len(first_ranks) if first_ranks else 0.0


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion; honest about small samples."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Probability a random positive outranks a random negative (ties count half)."""
    if not positives or not negatives:
        return float("nan")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def percentile_rank(score: float, scores: Sequence[float]) -> float:
    """Share of `scores` strictly below `score` (1.0 = riskiest)."""
    others = list(scores)
    return sum(1 for s in others if s < score) / len(others) if others else 0.0


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def pct(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.0f}%"
