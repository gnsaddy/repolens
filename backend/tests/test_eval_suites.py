import json
from pathlib import Path

from repolens.evals.suites import __main__ as suites
from repolens.evals.suites import docs, memory, routing
from repolens.evals.suites.corpus import TOPICS, UNANSWERABLE

BASELINE = Path(__file__).resolve().parents[2] / "evals" / "baselines" / "suites.json"


def test_corpus_is_consistent():
    assert len(TOPICS) >= 20 and len({t.path for t in TOPICS}) == len(TOPICS) and len(UNANSWERABLE) >= 5
    assert all(t.exact and t.paraphrase and t.confusable and t.body for t in TOPICS)


def test_docs_suite_measures_retrieval_in_each_question_style():
    r = docs.run()
    assert r["pages"] == len(TOPICS) and r["questions"] == 3 * len(TOPICS)
    assert r["exact_top1"] >= 0.9  # the page's own words must find it
    assert r["paraphrase_top1"] < r["exact_top1"]  # and re-wording is where the local embedder is weak (known, tracked)
    assert 0.5 <= r["coverage_auc"] <= 1 and r["top1_ci95"][0] < r["top1"] < r["top1_ci95"][1]


def test_memory_suite_finds_thresholds_that_separate_rewordings_from_different_questions():
    r = memory.run()
    assert r["pairs"] >= 100 and r["precision"] >= 0.95  # the shipped thresholds almost never reuse a wrong answer
    assert 0 < r["recall"] < 1 and r["best_recall_at_95_precision"] >= r["recall"] - 1e-9


def test_routing_suite_never_sends_hard_work_to_a_weak_model_and_saves_money():
    r = routing.run()
    assert r["savings_hard_under_routed"] == 0 and r["normal_hard_under_routed"] == 0
    assert r["savings_cost_vs_all_strong"] < r["normal_cost_vs_all_strong"] < 0.7


def test_the_gate_flags_regressions_and_ignores_noise():
    base = {"docs": {"top1": 0.8, "mrr": 0.85}, "routing": {"savings_hard_under_routed": 0.0}}
    assert (
        suites.compare({"docs": {"top1": 0.78, "mrr": 0.84}, "routing": {"savings_hard_under_routed": 0.0}}, base) == []
    )
    bad = suites.compare({"docs": {"top1": 0.6, "mrr": 0.85}, "routing": {"savings_hard_under_routed": 0.1}}, base)
    assert len(bad) == 2 and "docs.top1" in bad[0] and "savings_hard_under_routed" in bad[1]
    assert suites.compare({"docs": {"top1": 0.99}}, {"docs": {}}) == []  # a metric with no baseline is not a failure


def test_current_results_pass_the_committed_baseline():
    assert BASELINE.exists(), (
        "record one with: python -m repolens.evals.suites --baseline ../evals/baselines/suites.json --update-baseline"
    )
    res = {"docs": docs.run(), "memory": memory.run(), "routing": routing.run()}
    assert suites.compare(res, json.loads(BASELINE.read_text())) == []
