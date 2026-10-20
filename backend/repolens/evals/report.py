"""Markdown + JSON report for a benchmark run."""

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import metrics as m
from .harness import Run


def _runs(res: dict, cfg: str, scope: str) -> list[Run]:
    return [r for r in res["runs"] if r.config == cfg and r.scope == scope]


def _ci(k: int, n: int) -> str:
    lo, hi = m.wilson(k, n)
    return f"{k / n * 100:.0f}% [{lo * 100:.0f}–{hi * 100:.0f}]" if n else "n/a"


def table_accuracy(res: dict, scope: str) -> list[str]:
    lines = [
        "| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    prev_top1: dict[str, bool] | None = None
    for cfg in res["configs"]:
        rs = _runs(res, cfg.key, scope)
        if not rs:
            continue
        n = len(rs)
        fr = [r.first_rank_symbol for r in rs]
        top1 = sum(1 for x in fr if x == 1)
        commit = [r.commit_top1 for r in rs if r.commit_top1 is not None]
        lines.append(
            f"| {cfg.name} | {_ci(top1, n)} | {m.pct(m.recall_at_k(fr, 3))} | {m.pct(m.recall_at_k(fr, 5))} | "
            f"{m.pct(m.recall_at_k(fr, 10))} | {m.mrr(fr):.2f} | "
            f"{m.pct(sum(1 for r in rs if r.first_rank_file == 1) / n)} | "
            f"{(str(sum(commit)) + '/' + str(len(commit))) if commit else 'n/a'} | "
            f"{statistics.median([r.duration_ms for r in rs]):.0f} ms |"
        )
        cur = {r.case_id: r.first_rank_symbol == 1 for r in rs}
        if prev_top1 is not None:
            gained = sum(1 for k, v in cur.items() if v and not prev_top1.get(k))
            lost = sum(1 for k, v in cur.items() if not v and prev_top1.get(k))
            lines[-1] += f"  <!-- vs previous: +{gained} / -{lost} -->"
        prev_top1 = cur
    return lines


def table_flips(res: dict, scope: str) -> list[str]:
    lines = ["| Step | Cases fixed | Cases broken | Net |", "|---|---|---|---|"]
    prev = None
    for cfg in res["configs"]:
        rs = _runs(res, cfg.key, scope)
        cur = {r.case_id: r.first_rank_symbol == 1 for r in rs}
        if prev is not None:
            gained = sum(1 for k, v in cur.items() if v and not prev[1].get(k))
            lost = sum(1 for k, v in cur.items() if not v and prev[1].get(k))
            lines.append(f"| {prev[0]} → {cfg.name} | {gained} | {lost} | {gained - lost:+d} |")
        prev = (cfg.name, cur)
    return lines


def table_by_template(res: dict, scope: str, cfg_key: str) -> list[str]:
    by: dict[str, list[bool]] = defaultdict(list)
    tmpl = {c.id: (c.template, c.variant) for c in res["cases"]}
    for r in _runs(res, cfg_key, scope):
        by[tmpl[r.case_id][0]].append(r.first_rank_symbol == 1)
    lines = ["| Regression type | Cases | Top-1 correct |", "|---|---|---|"]
    for t, v in sorted(by.items()):
        lines.append(f"| {t} | {len(v)} | {sum(v)}/{len(v)} |")
    return lines


def table_by_variant(res: dict, scope: str) -> list[str]:
    var = {c.id: c.variant for c in res["cases"]}
    names = sorted({v for v in var.values()})
    head = "| Configuration | " + " | ".join(f"{v} (n={sum(1 for x in var.values() if x == v)})" for v in names) + " |"
    lines = [head, "|---|" + "---|" * len(names)]
    for cfg in res["configs"]:
        rs = _runs(res, cfg.key, scope)
        cells = []
        for v in names:
            sub = [r for r in rs if var[r.case_id] == v]
            cells.append(f"{sum(1 for r in sub if r.first_rank_symbol == 1)}/{len(sub)}" if sub else "—")
        lines.append(f"| {cfg.name} | " + " | ".join(cells) + " |")
    return lines


def table_rca(res: dict, scope: str) -> list[str]:
    lines = [
        "| Configuration | Evidence support rate | Unsupported hypothesis rate | Citations match source | Citation covers the changed lines | Evidence kinds produced |",
        "|---|---|---|---|---|---|",
    ]
    for cfg in res["configs"]:
        rs = _runs(res, cfg.key, scope)
        sup = [r.supported_frac for r in rs if r.supported_frac is not None]
        ok = [r.citations_ok for r in rs if r.citations_ok is not None]
        gold = [r.citation_hits_gold for r in rs if r.citation_hits_gold is not None]
        kinds = sorted({k for r in rs for k in r.evidence_kinds})
        lines.append(
            f"| {cfg.name} | {m.pct(m.mean(sup)) if sup else 'n/a'} | {m.pct(1 - m.mean(sup)) if sup else 'n/a'} | "
            f"{m.pct(sum(ok) / len(ok)) if ok else 'n/a'} | {m.pct(sum(gold) / len(gold)) if gold else 'n/a'} | "
            f"{', '.join(kinds) or '—'} |"
        )
    return lines


def table_cost(res: dict, scope: str) -> list[str]:
    lines = [
        "| Configuration | Cases | LLM calls | Input tokens | Output tokens | Cost | Cache hits | Retry overhead (failed calls) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cfg in res["configs"]:
        rs = [r for r in _runs(res, cfg.key, scope) if cfg.kind == "workflow"]
        if not rs:
            continue
        lines.append(
            f"| {cfg.name} | {len(rs)} | {sum(r.llm_calls for r in rs)} | {sum(r.tokens_in for r in rs)} | "
            f"{sum(r.tokens_out for r in rs)} | ${sum(r.cost for r in rs):.4f} | {sum(r.cache_hits for r in rs)} | "
            f"{sum(r.failed_calls for r in rs)} |"
        )
    return lines


def render(res: dict) -> str:
    cases = res["cases"]
    idx = res["index"]
    inc = res["incremental"]
    risk = res["risk"]
    memory_cfg = next((c for c in res["configs"] if c.memory_pass), None)
    rec = [c for c in cases if c.recurrence_of]
    out = [
        "# RepoLens evaluation report",
        "",
        f"_Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · dataset `synthetic-v1` · seed {res['seed']} · "
        f"{len(cases)} cases · {res['elapsed_s']} s · offline mode (hash embeddings, no LLM calls)_",
        "",
        "## How to read this",
        "",
        "* **Synthetic, single-regression, Python-only benchmark.** Each case is a generated project with decoy functions, "
        "noise commits and misleading commit messages; ground truth is the injected commit, file, symbol and changed lines.",
        f"* **n = {len(cases)}**: differences of one or two cases are not meaningful; see the confidence intervals. Results "
        "describe this benchmark, not real-world accuracy.",
        "* Localisation is correct when the ranked list's entry matches the gold repository, file and symbol "
        "(module constants match on file). Configurations 1–3 rank retrieved chunks; 4–6 rank the investigation's hypotheses, "
        "filled with hybrid retrieval to depth 30 so Recall@K is comparable.",
        "",
    ]
    for scope, title in (
        ("case", "Repository known (search scoped to the affected repository)"),
        ("org", f"Repository unknown (search across all {len(cases)} repositories in the organisation)"),
    ):
        if scope not in res["scopes"]:
            continue
        out += (
            [f"## Root-cause localisation — {title}", ""]
            + table_accuracy(res, scope)
            + ["", "**Does each component help? (cases flipped between consecutive rows)**", ""]
            + table_flips(res, scope)
            + [""]
        )
        out += ["**By how the bug was reported**", ""] + table_by_variant(res, scope) + [""]
        out += (
            ["**By regression type — full pipeline**", ""]
            + table_by_template(res, scope, res["configs"][-1].key)
            + [""]
        )
        out += ["**Evidence quality**", ""] + table_rca(res, scope) + [""]
    if memory_cfg and rec:
        out += ["## Local memory", ""]
        for scope in res["scopes"]:
            full = {r.case_id: r for r in _runs(res, memory_cfg.key, scope)}
            hist = {r.case_id: r for r in _runs(res, "history", scope)} or {}
            graph_ = {r.case_id: r for r in _runs(res, "graph", scope)}
            ref = hist if hist else graph_
            first = [c for c in cases if not c.recurrence_of]
            hits = sum(1 for c in rec if full[c.id].memory_hits)
            out.append(
                f"* **{scope}-scope:** {len(rec)} recurring-incident cases; memory surfaced a similar prior incident in "
                f"{hits}/{len(rec)}. Top-1 on recurrences: full pipeline "
                f"{sum(1 for c in rec if full[c.id].first_rank_symbol == 1)}/{len(rec)} vs "
                f"history-only {sum(1 for c in rec if ref and ref[c.id].first_rank_symbol == 1)}/{len(rec)}; "
                f"on first-seen cases {sum(1 for c in first if full[c.id].first_rank_symbol == 1)}/{len(first)} vs "
                f"{sum(1 for c in first if ref and ref[c.id].first_rank_symbol == 1)}/{len(first)}."
            )
        out += [
            "",
            "Memory stores incident *prose* (title + root-cause text); recurrences here live in different repositories with different file "
            "names, so a text match rarely points at the right file. It is expected to help most for recurring bugs in the same repository.",
            "",
        ]
    out += ["## Cost", ""] + table_cost(res, "case") + [""]
    total_in = sum(r.tokens_in for r in res["runs"])
    if total_in == 0:
        out += [
            "No language model was configured, so no tokens were used and cost per successful RCA is $0.00 by construction. "
            "Re-run with `REPOLENS_LLM_PROVIDER` set to measure real token use, cache hits and retry overhead; "
            "the harness records them per run.",
            "",
        ]
    out += [
        "## Indexing",
        "",
        f"* Full index: median {statistics.median([i['ms'] for i in idx]):.0f} ms per repository "
        f"({statistics.median([i['files'] for i in idx]):.0f} files, {statistics.median([i['chunks'] for i in idx]):.0f} chunks).",
        f"* Incremental (one commit touching one file, {inc['samples']} samples): mean {inc['mean_ms']:.0f} ms, "
        f"{inc['mean_written']:.1f} chunks re-embedded vs {inc['mean_reused']:.1f} reused.",
        "",
    ]
    if risk:
        a_, x_ = risk["all"], risk["ex_initial"]
        out += [
            "## Change-risk scoring (Oracle)",
            "",
            f"Task: does the commit that introduced the regression score higher than the other commits in the same history? "
            f"({risk['cases']} cases, ~{a_['mean_commits']:.0f} commits each; scores use only information available before each commit.) "
            f"The highest-scored commit is the large initial import in {m.pct(risk['top_scored_is_initial_import'])} of cases, "
            "so the second column excludes it — nobody risk-scores a first commit.",
            "",
            "| Metric | All commits | Excluding initial import | Chance (excl.) |",
            "|---|---|---|---|",
            f"| AUC (bug commit vs other commits) | {a_['auc']:.2f} | {x_['auc']:.2f} | 0.50 |",
            f"| Bug commit ranked #1 | {m.pct(a_['top1'])} | {m.pct(x_['top1'])} | {m.pct(x_['chance_top1'])} |",
            f"| Bug commit in top 3 | {m.pct(a_['top3'])} | {m.pct(x_['top3'])} | {m.pct(x_['chance_top3'])} |",
            f"| Mean percentile of the bug commit | {a_['mean_percentile']:.2f} | {x_['mean_percentile']:.2f} | 0.50 |",
            f"| Mean score: bug commit / other commits | {a_['mean_gold_score']:.1f} / {a_['mean_other_score']:.1f} | "
            f"{x_['mean_gold_score']:.1f} / {x_['mean_other_score']:.1f} | — |",
            "",
            f"Baseline — change size alone: AUC {risk['auc_size_only']:.2f} (below 0.5 because these injected regressions are "
            "*smaller* than the average commit). Compare the Oracle's AUC with that, and with chance, not with 1.0.",
            "",
            "Caveat: most other commits in these synthetic histories are docs, test or config edits — easy negatives. "
            "Telling a risky *code* change from an equally sized safe *code* change is harder and is not what this "
            "measures; treat the AUC as evidence the signals point the right way, and as a calibration target once real "
            "labelled incidents exist.",
            "",
        ]
    out += [
        "## Not measured",
        "",
        "* **Fix metrics** (patch applies, regression test detects the bug, validation pass rate): fix generation is not implemented.",
        "* **Human verification rate / acceptance rate:** requires real reviewers. The harness only simulates a reviewer for the memory pass.",
        "* **Real LLM narrative quality:** the benchmark measures retrieval, ranking and citation checks, which are deterministic.",
        "",
    ]
    return "\n".join(out)


def write(res: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render(res)
    (out_dir / "report.md").write_text(md)
    raw = {
        "seed": res["seed"],
        "elapsed_s": res["elapsed_s"],
        "index": res["index"],
        "incremental": res["incremental"],
        "risk": res["risk"],
        "runs": [
            {**{k: v for k, v in r.__dict__.items() if k != "ranked"}, "top5": [list(x) for x in r.ranked[:5]]}
            for r in res["runs"]
        ],
    }
    (out_dir / "results.json").write_text(json.dumps(raw, indent=2, default=str))
    return out_dir / "report.md"
