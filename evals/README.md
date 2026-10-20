# Evaluation

Measures whether each architectural component earns its place, using **known regressions** rather than impressions
(`idea.md` §20). Everything runs offline and deterministically.

```bash
cd backend
python -m repolens.evals --out ../evals/results/my-run --cases 20 --seed 7     # ~1 min
python -m repolens.evals --cases 10 --no-org-wide --no-risk                    # quick smoke
```

Outputs `report.md` (tables), `results.json` (per-case runs) and `manifest.json` (the dataset's ground truth).
Committed results: [`results/dev-seed7`](results/dev-seed7/report.md) and [`results/heldout-seed123`](results/heldout-seed123/report.md).

## Dataset: `synthetic-v1`

`backend/repolens/evals/generator.py` generates, per case, a small Python project (service, repository, API, config,
utils, tests, docs) with decoy functions, 2–3 noise commits, a docstring edit in the *same file* as the bug, **one
injected regression with a deliberately uninformative commit message** (“refactor: tidy …”), and a trailing docs
commit. Ten regression types: removed null guard, off-by-one paging, retry removed, validation removed,
case-sensitivity, rounding, tenant-less cache key, wrong default constant, swapped arguments, wrong status code.
Bugs are reported three ways: *trace* (a traceback), *behavioral* (symptoms only), *feature* (symptoms + the route).
Ground truth: gold commit, file, symbol and changed lines. Cases 11–20 repeat a template in another domain (for memory).

## Configurations (ablation)

1. Keyword search · 2. Semantic RAG (vector only) · 3. Hybrid RAG (vector + keyword + stack frames) ·
4. + Git history (diffs and changed lines) · 5. + git blame of the failing line · 6. + dependency graph (instead of
history) · 7. Full investigation (history + blame + graph) + local memory · 8. Hybrid RAG + reranker · 9. Full investigation + reranker.
Configurations 1–7 explicitly run without a reranker so they stay comparable whatever the server default is.

## Headline results (20 cases per seed; repository known; Top-1 = right file *and* function)

| Configuration | dev seed 7 | held-out seed 123 |
|---|---|---|
| 1. Keyword | 35% [18–57] | 30% [15–52] |
| 2. Semantic (vector only, hash embeddings) | 0% [0–16] | 5% [1–24] |
| 3. Hybrid RAG | 25% [11–47] | 25% [11–47] |
| 4. + Git history (diffs, changed lines) | **70% [48–85]** | **75% [53–89]** |
| 5. + git blame of the failing line | 70% [48–85] | 75% [53–89] |
| 6. + dependency graph (no history) | 15% [5–36] | 10% [3–30] |
| 7. Full + memory | 70% [48–85] | 75% [53–89] |
| 8. Hybrid RAG + reranker | 25% [11–47] | 30% [15–52] |
| 9. Full + reranker | 75% [53–89] | 80% [58–92] |

Brackets are 95% Wilson intervals. The held-out seed was run once, after the code was frozen.

**What this says**
* **The offline reranker helps ranking quality, not (yet) top-1.** Hybrid + reranker leaves top-1 at 25% / 30% (inside
  the confidence interval of the 25% baseline) but raises Top-3 (50→65% on seed 7, unchanged at 60% on seed 123), Recall@10
  (70→85%, 75→90%) and MRR (0.42→0.46, 0.45→0.49). With git history on top, MRR goes 0.75→0.81 and 0.79→0.85, and
  Top-3 75/80%→90%. Gains are consistent across both seeds but small samples: treat them as "probably helps", not
  proven. A hosted cross-encoder (`REPOLENS_RERANK_PROVIDER=api`) was not evaluated.
* **Git history is the component that matters**: it turns “the right file” (hybrid 35–40%) into “the right function and
  the commit that broke it” (70–75% top-1, 15–18/20 commit attribution). Citations always matched the source text.
* **Git blame is neutral here.** Blaming whole functions *hurt* (the original author of most lines is rarely the
  regression: commit attribution fell from 15/20 to 14/20 and 18/20 to 16/20), so blame is applied only to the exact
  failing line named by a stack trace (classic SZZ). That changes nothing on this benchmark — only 2 of 20 cases per seed
  carry a traceback, and many injected bugs *delete* lines, which blame cannot see. It is kept because it is principled
  and harmless, and the blame endpoint is useful on its own; do not read it as evidence that blame helps.
* **The dependency graph and memory do not improve localisation here.** The graph adds explanatory evidence (callers,
  tests, docs) but does not influence ranking; memory stores prose, and recurrences live in different repositories.
  They may matter for explanation quality and for same-repo recurrences, which this benchmark does not measure.
* **Vector-only search with the offline hash embedder is poor on natural-language bug reports.** A real embedding
  model (`REPOLENS_EMBEDDING_PROVIDER=openai`) was not evaluated.
* **Repository unknown** (search across all 20 near-identical repositories) is far harder: top-1 ≈ 15%.
* **Change-risk (Oracle)**: AUC 0.87 for the regression commit vs other commits (excluding the initial import) on both
  seeds, vs 0.50 chance and 0.45–0.47 for change size alone. Most negatives are docs/test/config edits (easy).

## How the benchmark improved the product

| Step | History-config top-1 (dev) | MRR |
|---|---|---|
| First measurement | 40% | 0.49 |
| + use the suspect commit's changed lines to pick the *function* | 55% | 0.60 |
| + light stemming (“Reserving” ↔ `reserve`) | 70% | 0.74 |

Other defects found by measuring: non-deterministic tie-breaking (random row ids decided equal scores — now by file and
line), the failing frame of a Python traceback not being preferred over its callers, and a harness bug that leaked a
temporary commit (the first “0/6 commit attribution” was that bug, not the product).

## Limitations — read before quoting numbers

* **Synthetic and generated by the same author as the system**; fixes were tuned on seed 7 (hence the held-out seed),
  but both come from one generator. Real-world accuracy is unknown.
* Python only, one regression per repository, small repositories (~10 files), n = 20 per seed: intervals are wide.
* Offline mode: no LLM tokens or cost were measured (the harness records them when a model is configured); no human
  verification or acceptance rates; **fix-generation metrics are not applicable** (fix generation is not built).
