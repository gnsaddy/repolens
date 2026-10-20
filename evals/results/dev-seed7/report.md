# RepoLens evaluation report

_Generated 2026-10-20 17:32 UTC · dataset `synthetic-v1` · seed 7 · 20 cases · 76.1 s · offline mode (hash embeddings, no LLM calls)_

## How to read this

* **Synthetic, single-regression, Python-only benchmark.** Each case is a generated project with decoy functions, noise commits and misleading commit messages; ground truth is the injected commit, file, symbol and changed lines.
* **n = 20**: differences of one or two cases are not meaningful; see the confidence intervals. Results describe this benchmark, not real-world accuracy.
* Localisation is correct when the ranked list's entry matches the gold repository, file and symbol (module constants match on file). Configurations 1–3 rank retrieved chunks; 4–6 rank the investigation's hypotheses, filled with hybrid retrieval to depth 30 so Recall@K is comparable.

## Root-cause localisation — Repository known (search scoped to the affected repository)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 35% [18–57] | 45% | 50% | 70% | 0.45 | 50% | n/a | 5 ms |
| 2. Semantic RAG (vector only) | 0% [0–16] | 30% | 35% | 35% | 0.15 | 15% | n/a | 4 ms |  <!-- vs previous: +0 / -7 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 25% [11–47] | 50% | 65% | 70% | 0.42 | 40% | n/a | 5 ms |  <!-- vs previous: +5 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 70% [48–85] | 75% | 75% | 85% | 0.75 | 90% | 15/20 | 46 ms |  <!-- vs previous: +9 / -0 -->
| 5. Hybrid RAG + Git history + blame | 70% [48–85] | 75% | 75% | 85% | 0.75 | 90% | 15/20 | 48 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 15% [5–36] | 35% | 60% | 90% | 0.33 | 25% | n/a | 94 ms |  <!-- vs previous: +0 / -11 -->
| 7. Full investigation (history + blame + graph) + local memory | 70% [48–85] | 75% | 75% | 85% | 0.75 | 90% | 15/20 | 67 ms |  <!-- vs previous: +11 / -0 -->
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 25% [11–47] | 65% | 75% | 85% | 0.46 | 40% | n/a | 7 ms |  <!-- vs previous: +0 / -9 -->
| 9. Full investigation + reranker | 75% [53–89] | 90% | 90% | 90% | 0.81 | 90% | 16/20 | 70 ms |  <!-- vs previous: +10 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 0 | 7 | -7 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 5 | 0 | +5 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 9 | 0 | +9 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 11 | -11 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 11 | 0 | +11 |
| 7. Full investigation (history + blame + graph) + local memory → 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0 | 9 | -9 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) → 9. Full investigation + reranker | 10 | 0 | +10 |

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
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 3/12 | 1/6 | 1/2 |
| 9. Full investigation + reranker | 7/12 | 6/6 | 2/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 0/2 |
| case_sensitivity | 2 | 2/2 |
| missing_validation | 2 | 2/2 |
| null_guard | 2 | 1/2 |
| off_by_one | 2 | 2/2 |
| retry_removed | 2 | 2/2 |
| rounding | 2 | 2/2 |
| status_code | 2 | 1/2 |
| swapped_args | 2 | 2/2 |
| wrong_default | 2 | 1/2 |

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
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | n/a | n/a | 100% | 30% | — |
| 9. Full investigation + reranker | 100% | 0% | 100% | 80% | caller, code, commit, doc, test |

## Root-cause localisation — Repository unknown (search across all 20 repositories in the organisation)

| Configuration | Top-1 (95% CI) | Top-3 | Top-5 | Recall@10 | MRR | File top-1 | Commit top-1 | Median time |
|---|---|---|---|---|---|---|---|---|
| 1. Keyword search | 5% [1–24] | 15% | 15% | 20% | 0.12 | 15% | n/a | 92 ms |
| 2. Semantic RAG (vector only) | 0% [0–16] | 5% | 10% | 15% | 0.04 | 5% | n/a | 87 ms |  <!-- vs previous: +0 / -1 -->
| 3. Hybrid RAG (vector + keyword + stack frames) | 0% [0–16] | 10% | 10% | 25% | 0.08 | 10% | n/a | 92 ms |  <!-- vs previous: +0 / -0 -->
| 4. Hybrid RAG + Git history (diffs, changed lines) | 15% [5–36] | 15% | 20% | 20% | 0.18 | 25% | 3/20 | 174 ms |  <!-- vs previous: +3 / -0 -->
| 5. Hybrid RAG + Git history + blame | 15% [5–36] | 15% | 15% | 20% | 0.18 | 25% | 3/20 | 186 ms |  <!-- vs previous: +0 / -0 -->
| 6. Hybrid RAG + dependency graph | 0% [0–16] | 10% | 10% | 20% | 0.07 | 5% | n/a | 776 ms |  <!-- vs previous: +0 / -3 -->
| 7. Full investigation (history + blame + graph) + local memory | 15% [5–36] | 15% | 15% | 20% | 0.18 | 25% | 3/20 | 358 ms |  <!-- vs previous: +3 / -0 -->
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0% [0–16] | 5% | 5% | 20% | 0.07 | 10% | n/a | 96 ms |  <!-- vs previous: +0 / -3 -->
| 9. Full investigation + reranker | 15% [5–36] | 20% | 20% | 25% | 0.19 | 25% | 3/20 | 329 ms |  <!-- vs previous: +3 / -0 -->

**Does each component help? (cases flipped between consecutive rows)**

| Step | Cases fixed | Cases broken | Net |
|---|---|---|---|
| 1. Keyword search → 2. Semantic RAG (vector only) | 0 | 1 | -1 |
| 2. Semantic RAG (vector only) → 3. Hybrid RAG (vector + keyword + stack frames) | 0 | 0 | +0 |
| 3. Hybrid RAG (vector + keyword + stack frames) → 4. Hybrid RAG + Git history (diffs, changed lines) | 3 | 0 | +3 |
| 4. Hybrid RAG + Git history (diffs, changed lines) → 5. Hybrid RAG + Git history + blame | 0 | 0 | +0 |
| 5. Hybrid RAG + Git history + blame → 6. Hybrid RAG + dependency graph | 0 | 3 | -3 |
| 6. Hybrid RAG + dependency graph → 7. Full investigation (history + blame + graph) + local memory | 3 | 0 | +3 |
| 7. Full investigation (history + blame + graph) + local memory → 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0 | 3 | -3 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) → 9. Full investigation + reranker | 3 | 0 | +3 |

**By how the bug was reported**

| Configuration | behavioral (n=12) | feature (n=6) | trace (n=2) |
|---|---|---|---|
| 1. Keyword search | 0/12 | 0/6 | 1/2 |
| 2. Semantic RAG (vector only) | 0/12 | 0/6 | 0/2 |
| 3. Hybrid RAG (vector + keyword + stack frames) | 0/12 | 0/6 | 0/2 |
| 4. Hybrid RAG + Git history (diffs, changed lines) | 1/12 | 1/6 | 1/2 |
| 5. Hybrid RAG + Git history + blame | 1/12 | 1/6 | 1/2 |
| 6. Hybrid RAG + dependency graph | 0/12 | 0/6 | 0/2 |
| 7. Full investigation (history + blame + graph) + local memory | 1/12 | 1/6 | 1/2 |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | 0/12 | 0/6 | 0/2 |
| 9. Full investigation + reranker | 1/12 | 1/6 | 1/2 |

**By regression type — full pipeline**

| Regression type | Cases | Top-1 correct |
|---|---|---|
| cache_key | 2 | 0/2 |
| case_sensitivity | 2 | 0/2 |
| missing_validation | 2 | 0/2 |
| null_guard | 2 | 0/2 |
| off_by_one | 2 | 1/2 |
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
| 4. Hybrid RAG + Git history (diffs, changed lines) | 100% | 0% | 80% | 35% | code, commit |
| 5. Hybrid RAG + Git history + blame | 100% | 0% | 80% | 35% | code, commit |
| 6. Hybrid RAG + dependency graph | 0% | 100% | 65% | 20% | caller, code, doc, test |
| 7. Full investigation (history + blame + graph) + local memory | 100% | 0% | 80% | 35% | caller, code, commit, doc, test |
| 8. Hybrid RAG + reranker (offline lexical cross-scoring) | n/a | n/a | 100% | 20% | — |
| 9. Full investigation + reranker | 100% | 0% | 80% | 35% | caller, code, commit, doc, test |

## Local memory

* **case-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 2/10. Top-1 on recurrences: full pipeline 7/10 vs history-only 7/10; on first-seen cases 7/10 vs 7/10.
* **org-scope:** 10 recurring-incident cases; memory surfaced a similar prior incident in 0/10. Top-1 on recurrences: full pipeline 2/10 vs history-only 2/10; on first-seen cases 1/10 vs 1/10.

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

* Full index: median 92 ms per repository (10 files, 41 chunks).
* Incremental (one commit touching one file, 5 samples): mean 71 ms, 1.0 chunks re-embedded vs 40.8 reused.

## Change-risk scoring (Oracle)

Task: does the commit that introduced the regression score higher than the other commits in the same history? (20 cases, ~6 commits each; scores use only information available before each commit.) The highest-scored commit is the large initial import in 100% of cases, so the second column excludes it — nobody risk-scores a first commit.

| Metric | All commits | Excluding initial import | Chance (excl.) |
|---|---|---|---|
| AUC (bug commit vs other commits) | 0.69 | 0.86 | 0.50 |
| Bug commit ranked #1 | 0% | 60% | 20% |
| Bug commit in top 3 | 95% | 100% | 60% |
| Mean percentile of the bug commit | 0.69 | 0.86 | 0.50 |
| Mean score: bug commit / other commits | 34.0 / 22.2 | 34.0 / 14.6 | — |

Baseline — change size alone: AUC 0.45 (below 0.5 because these injected regressions are *smaller* than the average commit). Compare the Oracle's AUC with that, and with chance, not with 1.0.

Caveat: most other commits in these synthetic histories are docs, test or config edits — easy negatives. Telling a risky *code* change from an equally sized safe *code* change is harder and is not what this measures; treat the AUC as evidence the signals point the right way, and as a calibration target once real labelled incidents exist.

## Not measured

* **Fix metrics** (patch applies, regression test detects the bug, validation pass rate): fix generation is not implemented.
* **Human verification rate / acceptance rate:** requires real reviewers. The harness only simulates a reviewer for the memory pass.
* **Real LLM narrative quality:** the benchmark measures retrieval, ranking and citation checks, which are deterministic.
