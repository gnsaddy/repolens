import math
from pathlib import Path

import pytest

from repolens.evals import metrics as m
from repolens.evals import report
from repolens.evals.generator import DOMAINS, TEMPLATES, generate
from repolens.evals.harness import CONFIGS, run_benchmark
from repolens.services import gitops


# ------------------------------------------------------------------ metrics
def test_ranking_metrics():
    assert m.first_rank([False, False, True]) == 3 and m.first_rank([False]) is None
    ranks = [1, 3, None, 12]
    assert m.recall_at_k(ranks, 1) == 0.25 and m.recall_at_k(ranks, 5) == 0.5 and m.recall_at_k(ranks, 10) == 0.5
    assert m.mrr(ranks) == pytest.approx((1 + 1 / 3 + 1 / 12) / 4)
    assert m.recall_at_k([], 3) == 0.0 and m.mrr([]) == 0.0


def test_wilson_interval_is_honest_for_small_samples():
    lo, hi = m.wilson(0, 6)
    assert lo == 0.0 and 0.3 < hi < 0.5  # 0/6 is still compatible with a ~40% true rate
    lo, hi = m.wilson(10, 20)
    assert lo < 0.5 < hi and hi - lo < 0.5
    assert m.wilson(0, 0) == (0.0, 0.0)
    lo2, hi2 = m.wilson(100, 200)
    assert (hi2 - lo2) < (hi - lo)  # more data -> tighter


def test_auc_and_percentile():
    assert m.auc([3.0], [1.0, 2.0]) == 1.0 and m.auc([1.0], [2.0, 3.0]) == 0.0
    assert m.auc([2.0], [2.0, 2.0]) == 0.5  # ties count half
    assert math.isnan(m.auc([], [1.0]))
    assert m.percentile_rank(5, [1, 2, 3, 9]) == 0.75 and m.percentile_rank(1, []) == 0.0
    assert m.pct(None) == "n/a" and m.pct(0.256) == "26%"


# ------------------------------------------------------------------ generator
@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("evalgen")
    return out, generate(out, 12, seed=7)


def test_generation_is_deterministic(dataset, tmp_path):
    _, cases = dataset
    again = generate(tmp_path, 12, seed=7)
    assert [c.gold_commit for c in cases] == [c.gold_commit for c in again]
    other = generate(tmp_path / "b", 12, seed=8)
    assert [c.gold_commit for c in cases] != [c.gold_commit for c in other]


def test_ground_truth_points_at_the_injected_change(dataset):
    out, cases = dataset
    assert len({c.id for c in cases}) == len(cases) and {c.template for c in cases} == {t.id for t in TEMPLATES}
    for c in cases:
        repo = str(out / c.repo_dir)
        hunks = gitops.commit_hunks(repo, c.gold_commit)
        assert set(hunks) == {c.gold_file}, c.id  # the regression commit touches exactly the gold file
        assert any(s <= c.gold_lines[1] and c.gold_lines[0] <= e for s, e in hunks[c.gold_file])
        log = gitops.log(repo, limit=50)
        assert len(log) >= 4 and sum(1 for x in log if x.sha == c.gold_commit) == 1
        assert c.bug_report.strip() and c.domain in {d["pkg"] for d in DOMAINS}


def test_commit_messages_do_not_leak_the_answer(dataset):
    out, cases = dataset
    for c in cases:
        msg = next(x.message for x in gitops.log(str(out / c.repo_dir), limit=50) if x.sha == c.gold_commit)
        sym = c.gold_symbol.split(".")[-1] if c.gold_symbol else None
        assert not any(w in msg for w in ("fix", "bug", "regress", "break")) and (sym is None or sym not in msg)


def test_recurrence_cases_repeat_a_template_in_another_domain(dataset):
    _, cases = dataset
    by_id = {c.id: c for c in cases}
    rec = [c for c in cases if c.recurrence_of]
    assert len(rec) == 2 and all(by_id[c.recurrence_of].template == c.template for c in rec)


def test_traceback_variant_cites_real_lines(dataset):
    out, cases = dataset
    traced = [c for c in cases if c.variant == "trace"]
    assert traced
    for c in traced:
        assert 'File "' in c.bug_report and c.gold_file in c.bug_report or "service.py" in c.bug_report


# ------------------------------------------------------------------ harness
@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    out = tmp_path_factory.mktemp("bench")
    return run_benchmark(out, 4, 7, org_wide=False, risk=True, progress=lambda *_: None), out


def runs(res, cfg):
    return [r for r in res["runs"] if r.config == cfg]


def test_every_configuration_runs_for_every_case(bench):
    res, _ = bench
    assert len(res["runs"]) == len(CONFIGS) * 4
    assert {r.config for r in res["runs"]} == {c.key for c in CONFIGS}
    assert all(r.ranked and len(r.ranked) <= 30 for r in res["runs"])


def test_ablation_switches_really_remove_components(bench):
    res, _ = bench
    kinds = {c.key: {k for r in runs(res, c.key) for k in r.evidence_kinds} for c in CONFIGS}
    assert "commit" not in kinds["graph"] and "commit" in kinds["history"] and "commit" in kinds["full"]
    assert not ({"doc", "test", "caller"} & kinds["history"])  # graph off -> no graph evidence
    assert {"doc", "test", "caller"} & kinds["graph"] and {"doc", "test", "caller"} & kinds["full"]
    assert all(r.memory_hits == 0 for k in ("history", "graph") for r in runs(res, k))
    assert all(r.commit_top1 is None for k in ("keyword", "semantic", "hybrid", "graph") for r in runs(res, k))


def test_keyword_and_vector_modes_rank_differently(bench):
    res, _ = bench
    kw, sem = runs(res, "keyword"), runs(res, "semantic")
    assert any(a.ranked != b.ranked for a, b in zip(kw, sem, strict=True))


def test_citations_are_faithful_to_the_source(bench):
    res, _ = bench
    for cfg in CONFIGS:
        ok = [r.citations_ok for r in runs(res, cfg.key) if r.citations_ok is not None]
        assert ok and all(ok), cfg.key  # a cited snippet always matches the file at that location


def test_incremental_indexing_reuses_unchanged_chunks(bench):
    res, _ = bench
    inc = res["incremental"]
    assert inc["samples"] == 4 and inc["mean_written"] <= 2 and inc["mean_reused"] > inc["mean_written"]


def test_no_temporary_commits_leak_into_the_index(bench):
    from repolens.db import session_factory
    from repolens.models import Change

    assert session_factory()().query(Change).filter(Change.message == "eval: extra").count() == 0


def test_risk_evaluation_is_computed_without_leakage(bench):
    res, _ = bench
    r = res["risk"]
    assert r["cases"] == 4 and 0.0 <= r["all"]["auc"] <= 1.0 and 0.0 <= r["auc_size_only"] <= 1.0
    assert all(1 <= p["rank"] <= p["of"] for p in r["per_case"])
    assert r["ex_initial"]["mean_commits"] == pytest.approx(r["all"]["mean_commits"] - 1)  # initial import removed
    assert 0 < r["ex_initial"]["chance_top3"] <= 1 and r["ex_initial"]["chance_top1"] < r["ex_initial"]["chance_top3"]
    assert 0.0 <= r["top_scored_is_initial_import"] <= 1.0


def test_report_renders_all_sections_and_states_limits(bench, tmp_path):
    res, _ = bench
    path = report.write(res, tmp_path)
    md = path.read_text()
    for needle in (
        "# RepoLens evaluation report",
        "Root-cause localisation",
        "95% CI",
        "Does each component help",
        "Evidence quality",
        "Indexing",
        "Change-risk scoring",
        "Not measured",
        "Fix metrics",
        "Synthetic",
    ):
        assert needle in md, needle
    assert "No language model was configured" in md  # cost section is honest about offline mode
    assert (tmp_path / "results.json").exists()


def test_benchmark_is_reproducible(bench, tmp_path):
    res, _ = bench
    again = run_benchmark(tmp_path, 4, 7, org_wide=False, risk=False, progress=lambda *_: None)
    key = lambda rs: [(r.case_id, r.config, r.first_rank_symbol, r.first_rank_file, r.commit_top1) for r in rs]  # noqa: E731
    assert key(res["runs"]) == key(again["runs"])
    assert isinstance(Path(tmp_path), Path)
