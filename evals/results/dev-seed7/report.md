# RepoLens evaluation report

_Generated 2026-10-19 07:35 UTC · dataset `synthetic-v1` · seed 7 · 20 cases · 60.6 s · offline mode (hash embeddings, no LLM calls)_

## How to read this

* **Synthetic, single-regression, Python-only benchmark.** Each case is a generated project with decoy functions, noise commits and misleading commit messages; ground truth is the injected commit, file, symbol and changed lines.
* **n = 20**: differences of one or two cases are not meaningful; see the confidence intervals. Results describe this benchmark, not real-world accuracy.
* Localisation is correct when the ranked list's entry matches the gold repository, file and symbol (module constants match on file). Configurations 1–3 rank retrieved chunks; 4–6 rank the investigation's hypotheses, filled with hybrid retrieval to depth 30 so Recall@K is comparable.

## Root-cause localisation — Repository known (search scoped to the affected repository)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 35% [18–57] | 45% | 50% | 70% | 0.45 | 50% | n/a | 6 ms |
| 2. Semantic RAG (vector only) | 0% [0–16] | 30% | 35% | 35% | 0.15 | 15% | n/a | 5 ms |  <!-- vs previous: +0 / -7 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 25% [11–47] | 50% | 65% | 70% | 0.42 | 40% | n/a | 6 ms |  <!-- vs previous: +5 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 70% [48–85] | 75% | 75% | 75% | 0.74 | 90% | 15/20 | 50 ms |  <!-- vs previous: +9 / -0 -->
| 5. Hybrid RAG + Git history + blame | 70% [48–85] | 75% | 75% | 75% | 0.74 | 90% | 15/20 | 47 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 15% [5–36] | 35% | 60% | 75% | 0.32 | 25% | n/a | 93 ms |  <!-- vs previous: +0 / -11 -->
| 7. Full investigation (history + blame + graph) + local memory | 70% [48–85] | 75% | 75% | 75% | 0.74 | 90% | 15/20 | 66 ms |  <!-- vs previous: +11 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 0 | 7 | -7 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 5 | 0 | +5 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 9 | 0 | +9 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 11 | -11 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 11 | 0 | +11 |

**By how the bug was reported**

| Configuration | behavioral (n=12) | feature (n=6) | trace (n=2) |
|---|---|---|---|
| 1. Keyword search | 5/12 | 1/6 | 1/2 |
| 2. Semantic RAG (vector only) | 0/12 | 0/6 | 0/2 |
| 3. Hybrid RAG (vector + keyword + stack frames) | 3/12 | 1/6 | 1/2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 8/12 | 4/6 | 2/2 |
| 5. Hybrid RAG + Git history + blame | 8/12 | 4/6 | 2/2 |
| 6. Hybrid RAG + dependency graph | 1/12 | 0/6 | 2/2 |
| 7. Full investigation (history + blame + graph) + local memory | 8/12 | 4/6 | 2/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 0/2 |
| case_sensitivity | 2 | 2/2 |
| missing_validation | 2 | 2/2 |
| null_guard | 2 | 1/2 |
| off_by_one | 2 | 2/2 |
| retry_removed | 2 | 2/2 |
| rounding | 2 | 1/2 |
| status_code | 2 | 2/2 |
| swapped_args | 2 | 2/2 |
| wrong_default | 2 | 0/2 |

**Evidence quality**

| Configuration | Evidence support rate | Unsupported hypothesis rate | Citations match source | Citation covers the changed lines | Evidence kinds produced |
|---|---|---|---|---|---|
| 1. Keyword search | n/a | n/a | 100% | 40% | — |
| 2. Semantic RAG (vector only) | n/a | n/a | 100% | 0% | — |
| 3. Hybrid RAG (vector + keyword + stack frames) | n/a | n/a | 100% | 30% | — |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 100% | 0% | 100% | 75% | code, commit |
| 5. Hybrid RAG + Git history + blame | 100% | 0% | 100% | 75% | code, commit |
| 6. Hybrid RAG + dependency graph | 2% | 98% | 100% | 15% | caller, code, doc, test |
| 7. Full investigation (history + blame + graph) + local memory | 100% | 0% | 100% | 75% | caller, code, commit, doc, test |

## Root-cause localisation — Repository unknown (search across all 20 repositories in the organisation)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 5% [1–24] | 15% | 15% | 20% | 0.12 | 15% | n/a | 90 ms |
| 2. Semantic RAG (vector only) | 0% [0–16] | 5% | 10% | 15% | 0.04 | 5% | n/a | 86 ms |  <!-- vs previous: +0 / -1 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 0% [0–16] | 10% | 10% | 25% | 0.08 | 10% | n/a | 93 ms |  <!-- vs previous: +0 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 10% [3–30] | 15% | 20% | 25% | 0.16 | 20% | 2/20 | 171 ms |  <!-- vs previous: +2 / -0 -->
| 5. Hybrid RAG + Git history + blame | 10% [3–30] | 15% | 15% | 25% | 0.15 | 20% | 2/20 | 186 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 0% [0–16] | 10% | 10% | 25% | 0.08 | 5% | n/a | 778 ms |  <!-- vs previous: +0 / -2 -->
| 7. Full investigation (history + blame + graph) + local memory | 10% [3–30] | 15% | 15% | 25% | 0.15 | 20% | 2/20 | 362 ms |  <!-- vs previous: +2 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 0 | 1 | -1 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 0 | 0 | +0 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 2 | 0 | +2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 2 | -2 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 2 | 0 | +2 |

**By how the bug was reported**

| Configuration | behavioral (n=12) | feature (n=6) | trace (n=2) |
|---|---|---|---|
| 1. Keyword search | 0/12 | 0/6 | 1/2 |
| 2. Semantic RAG (vector only) | 0/12 | 0/6 | 0/2 |
| 3. Hybrid RAG (vector + keyword + stack frames) | 0/12 | 0/6 | 0/2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 1/12 | 0/6 | 1/2 |
| 5. Hybrid RAG + Git history + blame | 1/12 | 0/6 | 1/2 |
| 6. Hybrid RAG + dependency graph | 0/12 | 0/6 | 0/2 |
| 7. Full investigation (history + blame + graph) + local memory | 1/12 | 0/6 | 1/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 0/2 |
| case_sensitivity | 2 | 0/2 |
| missing_validation | 2 | 0/2 |
| null_guard | 2 | 0/2 |
| off_by_one | 2 | 0/2 |
| retry_removed | 2 | 1/2 |
| rounding | 2 | 0/2 |
| status_code | 2 | 1/2 |
| swapped_args | 2 | 0/2 |
| wrong_default | 2 | 0/2 |

**Evidence quality**

| Configuration | Evidence support rate | Unsupported hypothesis rate | Citations match source | Citation covers the changed lines | Evidence kinds produced |
|---|---|---|---|---|---|
| 1. Keyword search | n/a | n/a | 100% | 25% | — |
| 2. Semantic RAG (vector only) | n/a | n/a | 100% | 0% | — |
| 3. Hybrid RAG (vector + keyword + stack frames) | n/a | n/a | 100% | 15% | — |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 100% | 0% | 75% | 35% | code, commit |
| 5. Hybrid RAG + Git history + blame | 100% | 0% | 75% | 35% | code, commit |
| 6. Hybrid RAG + dependency graph | 0% | 100% | 65% | 20% | caller, code, doc, test |
| 7. Full investigation (history + blame + graph) + local memory | 100% | 0% | 75% | 35% | caller, code, commit, doc, test |

## Local memory

* **case-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 2/10. Top-1 on recurrences: full pipeline 7/10 vs history-only 7/10; on first-seen cases 7/10 vs 7/10.
* **org-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 0/10. Top-1 on recurrences: full pipeline 1/10 vs history-only 1/10; on first-seen cases 1/10 vs 1/10.

Memory stores incident *prose* (title + root-cause text); recurrences here live in different repositories with different file names, so a text match rarely points at the right file. It is expected to help most for recurring bugs in the same repository.

## Cost

| Configuration | Cases | LLM calls | Input tokens | Output tokens | Cost | Cache hits | Retry overhead (failed calls) |
|---|---|---|---|---|---|---|---|
| 4. Hybrid RAG + Git history (diffs, changed lines) | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 5. Hybrid RAG + Git history + blame | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 6. Hybrid RAG + dependency graph | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 7. Full investigation (history + blame + graph) + local memory | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |

No language model was configured, so no tokens were used and cost per successful RCA is $0.00 by construction. Re-run with `REPOLENS_LLM_PROVIDER` set to measure real token use, cache hits and retry overhead; the harness records them per run.

## Indexing

* Full index: median 90 ms per repository (10 files, 41 chunks).
* Incremental (one commit touching one file, 5 samples): mean 74 ms, 1.0 chunks re-embedded vs 40.8 reused.

## Change-risk scoring (Oracle)

Task: does the commit that introduced the regression score higher than the other commits in the same history? (20 cases, ~6 commits each; scores use only information available before each commit.) The highest-scored commit is the large initial import in 100% of cases, so the second column excludes it — nobody risk-scores a first commit.

| Metric | All commits | Excluding initial import | Chance (excl.) |
|---|---|---|---|
| AUC (bug commit vs other commits) | 0.71 | 0.87 | 0.50 |
| Bug commit ranked #1 | 0% | 60% | 20% |
| Bug commit in top 3 | 95% | 100% | 60% |
| Mean percentile of the bug commit | 0.70 | 0.88 | 0.50 |
| Mean score: bug commit / other commits | 32.3 / 21.6 | 32.3 / 14.0 | — |

Baseline — change size alone: AUC 0.45 (below 0.5 because these injected regressions are *smaller* than the average commit). Compare the Oracle's AUC with that, and with chance, not with 1.0.

Caveat: most other commits in these synthetic histories are docs, test or config edits — easy negatives. Telling a risky *code* change from an equally sized safe *code* change is harder and is not what this measures; treat the AUC as evidence the signals point the right way, and as a calibration target once real labelled incidents exist.

## Not measured

* **Fix metrics** (patch applies, regression test detects the bug, validation pass rate): fix generation is not implemented.
* **Human verification rate / acceptance rate:** requires real reviewers. The harness only simulates a reviewer for the memory pass.
* **Real LLM narrative quality:** the benchmark measures retrieval, ranking and citation checks, which are deterministic.
