# RepoLens evaluation report

_Generated 2026-10-20 17:34 UTC · dataset `synthetic-v1` · seed 123 · 20 cases · 77.5 s · offline mode (hash embeddings, no LLM calls)_

## How to read this

* **Synthetic, single-regression, Python-only benchmark.** Each case is a generated project with decoy functions, noise commits and misleading commit messages; ground truth is the injected commit, file, symbol and changed lines.
* **n = 20**: differences of one or two cases are not meaningful; see the confidence intervals. Results describe this benchmark, not real-world accuracy.
* Localisation is correct when the ranked list's entry matches the gold repository, file and symbol (module constants match on file). Configurations 1–3 rank retrieved chunks; 4–6 rank the investigation's hypotheses, filled with hybrid retrieval to depth 30 so Recall@K is comparable.

## Root-cause localisation — Repository known (search scoped to the affected repository)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 30% [15–52] | 45% | 50% | 75% | 0.43 | 45% | n/a | 6 ms |
| 2. Semantic RAG (vector only) | 5% [1–24] | 30% | 35% | 35% | 0.18 | 15% | n/a | 5 ms |  <!-- vs previous: +1 / -6 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 25% [11–47] | 60% | 70% | 75% | 0.45 | 35% | n/a | 5 ms |  <!-- vs previous: +4 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 75% [53–89] | 80% | 80% | 85% | 0.79 | 90% | 18/20 | 49 ms |  <!-- vs previous: +10 / -0 -->
| 5. Hybrid RAG + Git history + blame | 75% [53–89] | 80% | 80% | 85% | 0.79 | 90% | 18/20 | 48 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 10% [3–30] | 40% | 55% | 90% | 0.32 | 25% | n/a | 92 ms |  <!-- vs previous: +0 / -13 -->
| 7. Full investigation (history + blame + graph) + local memory | 75% [53–89] | 80% | 80% | 85% | 0.79 | 90% | 18/20 | 69 ms |  <!-- vs previous: +13 / -0 -->
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 30% [15–52] | 60% | 75% | 90% | 0.49 | 40% | n/a | 7 ms |  <!-- vs previous: +0 / -9 -->
| 9. Full investigation + reranker | 80% [58–92] | 90% | 90% | 90% | 0.85 | 85% | 17/20 | 72 ms |  <!-- vs previous: +10 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 1 | 6 | -5 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 4 | 0 | +4 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 10 | 0 | +10 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 13 | -13 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 13 | 0 | +13 |
| 7. Full investigation (history + blame + graph) + local memory → 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0 | 9 | -9 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) → 9. Full investigation + reranker | 10 | 0 | +10 |

**By how the bug was reported**

| Configuration | behavioral (n=12) | feature (n=6) | trace (n=2) |
|---|---|---|---|
| 1. Keyword search | 5/12 | 0/6 | 1/2 |
| 2. Semantic RAG (vector only) | 1/12 | 0/6 | 0/2 |
| 3. Hybrid RAG (vector + keyword + stack frames) | 4/12 | 0/6 | 1/2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 9/12 | 4/6 | 2/2 |
| 5. Hybrid RAG + Git history + blame | 9/12 | 4/6 | 2/2 |
| 6. Hybrid RAG + dependency graph | 0/12 | 0/6 | 2/2 |
| 7. Full investigation (history + blame + graph) + local memory | 9/12 | 4/6 | 2/2 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 4/12 | 1/6 | 1/2 |
| 9. Full investigation + reranker | 9/12 | 5/6 | 2/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 2/2 |
| case_sensitivity | 2 | 2/2 |
| missing_validation | 2 | 2/2 |
| null_guard | 2 | 1/2 |
| off_by_one | 2 | 2/2 |
| retry_removed | 2 | 2/2 |
| rounding | 2 | 2/2 |
| status_code | 2 | 1/2 |
| swapped_args | 2 | 2/2 |
| wrong_default | 2 | 0/2 |

**Evidence quality**

| Configuration | Evidence support rate | Unsupported hypothesis rate | Citations match source | Citation covers the changed lines | Evidence kinds produced |
|---|---|---|---|---|---|
| 1. Keyword search | n/a | n/a | 100% | 35% | — |
| 2. Semantic RAG (vector only) | n/a | n/a | 100% | 6% | — |
| 3. Hybrid RAG (vector + keyword + stack frames) | n/a | n/a | 100% | 30% | — |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 100% | 0% | 100% | 80% | code, commit |
| 5. Hybrid RAG + Git history + blame | 100% | 0% | 100% | 80% | code, commit |
| 6. Hybrid RAG + dependency graph | 1% | 99% | 100% | 10% | caller, code, doc, test |
| 7. Full investigation (history + blame + graph) + local memory | 100% | 0% | 100% | 80% | caller, code, commit, doc, test |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | n/a | n/a | 100% | 35% | — |
| 9. Full investigation + reranker | 100% | 0% | 100% | 85% | caller, code, commit, doc, test |

## Root-cause localisation — Repository unknown (search across all 20 repositories in the organisation)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 5% [1–24] | 10% | 10% | 20% | 0.10 | 10% | n/a | 91 ms |
| 2. Semantic RAG (vector only) | 0% [0–16] | 5% | 15% | 15% | 0.05 | 0% | n/a | 84 ms |  <!-- vs previous: +0 / -1 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 0% [0–16] | 15% | 15% | 20% | 0.09 | 5% | n/a | 92 ms |  <!-- vs previous: +0 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 10% [3–30] | 15% | 20% | 20% | 0.15 | 15% | 3/20 | 170 ms |  <!-- vs previous: +2 / -0 -->
| 5. Hybrid RAG + Git history + blame | 10% [3–30] | 15% | 20% | 20% | 0.15 | 15% | 3/20 | 188 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 0% [0–16] | 0% | 0% | 15% | 0.04 | 0% | n/a | 800 ms |  <!-- vs previous: +0 / -2 -->
| 7. Full investigation (history + blame + graph) + local memory | 10% [3–30] | 15% | 20% | 20% | 0.15 | 15% | 3/20 | 346 ms |  <!-- vs previous: +2 / -0 -->
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0% [0–16] | 15% | 15% | 15% | 0.08 | 5% | n/a | 102 ms |  <!-- vs previous: +0 / -2 -->
| 9. Full investigation + reranker | 10% [3–30] | 15% | 20% | 20% | 0.15 | 20% | 3/20 | 355 ms |  <!-- vs previous: +2 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 0 | 1 | -1 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 0 | 0 | +0 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 2 | 0 | +2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 2 | -2 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 2 | 0 | +2 |
| 7. Full investigation (history + blame + graph) + local memory → 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0 | 2 | -2 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) → 9. Full investigation + reranker | 2 | 0 | +2 |

**By how the bug was reported**

| Configuration | behavioral (n=12) | feature (n=6) | trace (n=2) |
|---|---|---|---|
| 1. Keyword search | 0/12 | 0/6 | 1/2 |
| 2. Semantic RAG (vector only) | 0/12 | 0/6 | 0/2 |
| 3. Hybrid RAG (vector + keyword + stack frames) | 0/12 | 0/6 | 0/2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 0/12 | 1/6 | 1/2 |
| 5. Hybrid RAG + Git history + blame | 0/12 | 1/6 | 1/2 |
| 6. Hybrid RAG + dependency graph | 0/12 | 0/6 | 0/2 |
| 7. Full investigation (history + blame + graph) + local memory | 0/12 | 1/6 | 1/2 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0/12 | 0/6 | 0/2 |
| 9. Full investigation + reranker | 0/12 | 1/6 | 1/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 0/2 |
| case_sensitivity | 2 | 0/2 |
| missing_validation | 2 | 0/2 |
| null_guard | 2 | 0/2 |
| off_by_one | 2 | 0/2 |
| retry_removed | 2 | 2/2 |
| rounding | 2 | 0/2 |
| status_code | 2 | 0/2 |
| swapped_args | 2 | 0/2 |
| wrong_default | 2 | 0/2 |

**Evidence quality**

| Configuration | Evidence support rate | Unsupported hypothesis rate | Citations match source | Citation covers the changed lines | Evidence kinds produced |
|---|---|---|---|---|---|
| 1. Keyword search | n/a | n/a | 100% | 20% | — |
| 2. Semantic RAG (vector only) | n/a | n/a | 100% | 5% | — |
| 3. Hybrid RAG (vector + keyword + stack frames) | n/a | n/a | 100% | 25% | — |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 100% | 0% | 75% | 25% | code, commit |
| 5. Hybrid RAG + Git history + blame | 100% | 0% | 75% | 25% | code, commit |
| 6. Hybrid RAG + dependency graph | 0% | 100% | 55% | 5% | caller, code, doc, test |
| 7. Full investigation (history + blame + graph) + local memory | 100% | 0% | 75% | 25% | caller, code, commit, doc, test |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | n/a | n/a | 100% | 15% | — |
| 9. Full investigation + reranker | 100% | 0% | 75% | 20% | caller, code, commit, doc, test |

## Local memory

* **case-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 3/10. Top-1 on recurrences: full pipeline 8/10 vs history-only 8/10; on first-seen cases 7/10 vs 7/10.
* **org-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 2/10. Top-1 on recurrences: full pipeline 1/10 vs history-only 1/10; on first-seen cases 1/10 vs 1/10.

Memory stores incident *prose* (title + root-cause text); recurrences here live in different repositories with different file names, so a text match rarely points at the right file. It is expected to help most for recurring bugs in the same repository.

## Cost

| Configuration | Cases | LLM calls | Input tokens | Output tokens | Cost | Cache hits | Retry overhead (failed calls) |
|---|---|---|---|---|---|---|---|
| 4. Hybrid RAG + Git history (diffs, changed lines) | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 5. Hybrid RAG + Git history + blame | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 6. Hybrid RAG + dependency graph | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 7. Full investigation (history + blame + graph) + local memory | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |
| 9. Full investigation + reranker | 20 | 0 | 0 | 0 | $0.0000 | 0 | 0 |

No language model was configured, so no tokens were used and cost per successful RCA is $0.00 by construction. Re-run with `REPOLENS_LLM_PROVIDER` set to measure real token use, cache hits and retry overhead; the harness records them per run.

## Indexing

* Full index: median 90 ms per repository (10 files, 41 chunks).
* Incremental (one commit touching one file, 5 samples): mean 70 ms, 1.0 chunks re-embedded vs 40.6 reused.

## Change-risk scoring (Oracle)

Task: does the commit that introduced the regression score higher than the other commits in the same history? (20 cases, ~6 commits each; scores use only information available before each commit.) The highest-scored commit is the large initial import in 100% of cases, so the second column excludes it — nobody risk-scores a first commit.

| Metric | All commits | Excluding initial import | Chance (excl.) |
|---|---|---|---|
| AUC (bug commit vs other commits) | 0.71 | 0.90 | 0.50 |
| Bug commit ranked #1 | 0% | 70% | 22% |
| Bug commit in top 3 | 100% | 100% | 65% |
| Mean percentile of the bug commit | 0.71 | 0.90 | 0.50 |
| Mean score: bug commit / other commits | 35.6 / 22.7 | 35.6 / 14.4 | — |

Baseline — change size alone: AUC 0.47 (below 0.5 because these injected regressions are *smaller* than the average commit). Compare the Oracle's AUC with that, and with chance, not with 1.0.

Caveat: most other commits in these synthetic histories are docs, test or config edits — easy negatives. Telling a risky *code* change from an equally sized safe *code* change is harder and is not what this measures; treat the AUC as evidence the signals point the right way, and as a calibration target once real labelled incidents exist.

## Not measured

* **Fix metrics** (patch applies, regression test detects the bug, validation pass rate): fix generation is not implemented.
* **Human verification rate / acceptance rate:** requires real reviewers. The harness only simulates a reviewer for the memory pass.
* **Real LLM narrative quality:** the benchmark measures retrieval, ranking and citation checks, which are deterministic.
