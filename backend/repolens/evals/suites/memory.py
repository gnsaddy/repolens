"""Answer memory matching: re-worded repeats of a question must hit, different questions about the same page must
not. Scores every pair with the same two signals the application uses and finds the thresholds that separate them."""

from itertools import product

from ...config import get_settings
from .corpus import TOPICS
from .embedders import HASH, EmbedderSpec


def _pairs() -> list[tuple[str, str, bool]]:
    pairs: list[tuple[str, str, bool]] = []
    for t in TOPICS:
        pairs.append((t.exact, t.exact.lower().rstrip("?") + " ?", True))  # trivial re-typing
        pairs.append(
            (
                t.exact,
                t.exact.replace("How ", "In what way ", 1)
                if t.exact.startswith("How ")
                else "Please tell me: " + t.exact,
                True,
            )
        )
        pairs.append((t.exact, t.paraphrase, True))  # a real re-wording
        pairs.append((t.exact, t.confusable, False))  # same page, different question
    for a, b in zip(TOPICS, TOPICS[1:] + TOPICS[:1], strict=True):
        pairs.append((a.exact, b.exact, False))  # different pages
    return pairs


def _prf(rows: list[tuple[bool, bool]]) -> tuple[float, float, float]:
    tp = sum(1 for want, got in rows if want and got)
    fp = sum(1 for want, got in rows if not want and got)
    fn = sum(1 for want, got in rows if want and not got)
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def run(spec: EmbedderSpec = HASH) -> dict:
    from ...services import answer_memory as am
    from ...services.embeddings import cosine

    spec.apply()
    feats = []
    for a, b, want in _pairs():
        cos = cosine(am._embed(a), am._embed(b))
        wa, wb = am._words(a), am._words(b)
        jac = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
        feats.append((cos, jac, want))
    s = get_settings()

    def score(c_thr: float, j_thr: float) -> tuple[float, float, float]:
        return _prf([(want, cos >= c_thr or jac >= j_thr) for cos, jac, want in feats])

    p, r, f1 = score(s.answer_memory_threshold, s.answer_memory_word_threshold)
    best = (0.0, 0.0, 0.0, 1.0, 1.0)  # f1, precision, recall, cos threshold, jac threshold
    for c_thr, j_thr in product(
        [x / 100 for x in range(50, 100, 2)] + [1.01], [x / 100 for x in range(30, 100, 5)] + [1.01]
    ):
        pp, rr, ff = score(c_thr, j_thr)
        if pp >= 0.95 and (ff, pp) > (best[0], best[1]):
            best = (ff, pp, rr, c_thr, j_thr)
    return {
        "embedder": spec.name,
        "pairs": len(feats),
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(f1, 3),
        "best_f1_at_95_precision": round(best[0], 3),
        "best_recall_at_95_precision": round(best[2], 3),
        "suggested_cosine_threshold": best[3],
        "suggested_word_threshold": best[4],
        "wrong_reuse_rate": round(1 - p, 3),
    }
