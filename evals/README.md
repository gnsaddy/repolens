# Evaluation

Measures whether each architectural component earns its place, using **known regressions** rather than impressions. Everything runs offline and deterministically.

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


## Suites for documents, answer memory and routing

`python -m repolens.evals.suites` (about two seconds, offline) measures what people use every day, and is the quality gate in CI
(`make eval`; the baseline is `evals/baselines/suites.json`, record a new one with `make eval-baseline` only after an
improvement you have reviewed).

* **docs**: 24 documentation pages and 72 questions in three styles (`exact` words from the page, `paraphrase` in different words,
  `confusable` a different question about the same page), plus 8 questions the docs cannot answer. Reports top-1/3, recall@5, MRR,
  per-style results, and how well a signal separates answerable from unanswerable questions (AUC).
* **memory**: 120 question pairs (re-typings and re-wordings that should reuse an answer, different questions that must not);
  precision and recall at the shipped thresholds, and the best thresholds that keep precision at 95%.
* **routing**: 42 task shapes against three virtual models with realistic prices; cost against "always the strongest model" and
  how often a hard task lands on a weaker model (gated at zero). It does not measure answer quality: that needs a real model.

### Comparing embedders

```bash
# local server (Ollama): ollama pull nomic-embed-text
python -m repolens.evals.suites --suite docs,memory --embedder http --name nomic-embed-text \
  --embed-base-url http://localhost:11434/v1 --embed-model nomic-embed-text --embed-dim 768 --embed-no-dimensions
# hosted: the key is read from an environment variable, never from an argument
OPENAI_API_KEY=... python -m repolens.evals.suites --suite docs,memory --embedder http --name openai-small \
  --embed-base-url https://api.openai.com/v1 --embed-model text-embedding-3-small --embed-key-env OPENAI_API_KEY --embed-dim 256
```

Measured here (24 pages, 72 questions, offline, 2026-10-25; full numbers in `evals/results/embedders/`):

| | local hash (default) | nomic-embed-text (Ollama, 768 d) |
|---|---|---|
| top-1 | 79.2% | 83.3% |
| top-3 / recall@5 | 88.9% / 90.3% | 93.1% / 97.2% |
| MRR | 0.843 | 0.889 |
| paraphrased questions, top-1 | 41.7% | 54.2% |
| memory recall at the shipped thresholds (precision 100%) | 51.4% | 66.7% |
| memory F1 | 0.68 | 0.80 |

The model is better on every measure, most clearly where the question is worded differently from the page. With 72 questions the
top-1 difference is inside the confidence interval; the recall@5, paraphrase and memory differences are larger than the noise but
the set is small, so treat this as "clearly helps on re-wordings", not a precise figure. A hosted model was not run (it needs your
key). Neither embedder can tell an unanswerable question from an answerable one by the fused score (AUC about 0.5); the share of
the question's words found in the best passage does better (AUC about 0.75), which is recorded as `coverage_auc`.

**Refusing weak matches** ("the documents do not cover this") was measured and not shipped. Refusing when under a quarter of the question's
words are found in the best three passages catches 88% of unanswerable questions but wrongly refuses 58% of re-worded ones (31% of all
valid questions); at 15% it wrongly refuses 25% of re-worded questions and catches only 12%. The suite reports these rates
(`refuse_below_*`) so the idea can be re-tested with a different embedder.

**Default:** it stays the local hash embedder, because the default has to work with nothing installed and nothing sent out. To use
a model, set `REPOLENS_EMBEDDING_PROVIDER=openai` with the base URL, model and dimension, then run
`python -m repolens.devtools.reembed` (resizes the vector columns, clears the answer memory and marks repositories for
re-indexing). Settings → AI & search warns when the stored vectors came from a different model.

## Real bugs from a repository's own history (`bugs` suite)

`python -m repolens.evals.suites --suite bugs --repo /path/to/a/git/repo --cases 20` (or `make eval-bugs repo=/path`) needs no download and
sends nothing anywhere. It finds commits whose message says they fix something, treats the message as the bug report, rebuilds the
repository **as it was just before the fix** (later history is unreachable), indexes it, and checks whether RepoLens points at the
files the fix actually changed. It scores two things: plain retrieval, and the full investigation (its shortlist of suspects).
File names in the report are masked so the question is not trivial. No language model is called.

Measured on this project's own history (10 usable fix commits, offline, 2026-10-25; `evals/results/bugs-repolens-own-history.json`):

| | retrieval | investigation |
|---|---|---|
| true file in top 1 | 10% | 20% |
| top 3 | 30% (95% CI 11-60%) | 40% (CI 17-69%) |
| top 5 | 60% | 40% |
| MRR | 0.26 | 0.30 |
| picking files at random, top 3 | 2% | 2% |

How to read it honestly: ten cases is a small sample, so the intervals are wide; the report is a commit message, tidier than a real
ticket; and the repository is the one this software was built in. It is clearly far better than chance, and it is **not** good enough
to trust blindly: in six of ten cases the right file is not in the investigation's top 3. Two leads from the table: raw retrieval finds the
right file in the top 5 more often than the investigation's shortlist does (60% against 40%), so the shortlist is dropping good
candidates, and commits that only change configuration or tests are never found. Re-run it on your own repositories and quote your
numbers, not these.

## Is the confidence score trustworthy? (calibration)

The investigation confidence is a heuristic score. **Insights → "Can the confidence score be trusted?"** compares it with what people
confirmed (`GET /api/dashboard/calibration`): reliability by score band, expected calibration error, Brier score against always
predicting the base rate, and a bias label. After 20 reviewed investigations a monotone correction (pool-adjacent-violators) is
fitted. Until then the card says how many reviews are missing and that the score is a ranking, not a probability.
