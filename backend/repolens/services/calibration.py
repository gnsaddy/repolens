"""Is a stated confidence worth believing? Compares the score of each reviewed investigation with whether a person confirmed it.

The score is a heuristic, not a probability. With enough reviews we can say how it behaves in practice (reliability by band, Brier
score, expected calibration error) and fit a monotone correction (pool-adjacent-violators) that maps a raw score to the share of
investigations at that score that people actually confirmed. With too few reviews we say so instead of drawing a curve."""

MIN_FOR_CURVE = 20
BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))


def _bands(pairs: list[tuple[float, int]]) -> list[dict]:
    out = []
    for lo, hi in BANDS:
        sel = [(c, y) for c, y in pairs if lo <= c < hi]
        out.append(
            {
                "from": lo,
                "to": min(hi, 1.0),
                "n": len(sel),
                "mean_confidence": round(sum(c for c, _ in sel) / len(sel), 3) if sel else None,
                "confirmed_rate": round(sum(y for _, y in sel) / len(sel), 3) if sel else None,
            }
        )
    return out


def fit_isotonic(pairs: list[tuple[float, int]]) -> list[tuple[float, float]]:
    """Non-decreasing step function through the data (pool adjacent violators); returns (confidence, calibrated) knots."""
    blocks: list[list[float]] = []  # [sum_y, count, max_x]
    for x, y in sorted(pairs):
        blocks.append([float(y), 1.0, x])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            top = blocks.pop()
            blocks[-1] = [blocks[-1][0] + top[0], blocks[-1][1] + top[1], top[2]]
    return [(b[2], b[0] / b[1]) for b in blocks]


def adjust(confidence: float, curve: list[tuple[float, float]]) -> float:
    """The calibrated value for a raw score: the first step whose upper edge covers it (the last step beyond the data)."""
    for edge, value in curve:
        if confidence <= edge:
            return value
    return curve[-1][1] if curve else confidence


def report(pairs: list[tuple[float, int]]) -> dict:
    """`pairs` are (raw confidence 0..1, 1 if a person confirmed the cause else 0)."""
    n = len(pairs)
    base: dict = {"reviewed": n, "min_for_curve": MIN_FOR_CURVE, "bands": _bands(pairs)}
    if n == 0:
        return {
            **base,
            "status": "empty",
            "message": "No reviewed investigations yet. Confirm or reject root causes to see how far the confidence score can be trusted.",
        }
    brier = sum((c - y) ** 2 for c, y in pairs) / n
    ece = sum(b["n"] * abs(b["mean_confidence"] - b["confirmed_rate"]) for b in base["bands"] if b["n"]) / n
    rate = sum(y for _, y in pairs) / n
    out: dict = {
        **base,
        "confirmed_rate": round(rate, 3),
        "mean_confidence": round(sum(c for c, _ in pairs) / n, 3),
        "brier": round(brier, 4),
        "brier_if_always_base_rate": round(sum((rate - y) ** 2 for _, y in pairs) / n, 4),
        "ece": round(ece, 4),
    }
    gap = out["mean_confidence"] - out["confirmed_rate"]
    out["bias"] = "overconfident" if gap > 0.05 else "underconfident" if gap < -0.05 else "about right"
    if n < MIN_FOR_CURVE:
        return {
            **out,
            "status": "too_few",
            "message": f"{n} reviewed so far. At least {MIN_FOR_CURVE} are needed before a correction is fitted; treat the score as a ranking, not a probability.",
        }
    curve = fit_isotonic(pairs)
    return {
        **out,
        "status": "ok",
        "curve": [[round(x, 3), round(v, 3)] for x, v in curve],
        "message": f"Based on {n} reviewed investigations; the score is {out['bias']} on average.",
    }
