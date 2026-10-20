# Architecture

Modular monolith, as recommended in `idea.md` §5.3: one FastAPI app, in-process background workers, PostgreSQL + pgvector.

```
Vite/React SPA ──/api──▶ FastAPI ──▶ LangGraph RCA workflow ──▶ retrieval (pgvector + FTS + symbols + git history)
                           │                                   └▶ LLM adapter (optional) ─▶ usage events
                           ├▶ indexer (git → tree-sitter chunks → embeddings, incremental by content hash)
                           ├▶ Jira client (read issues, publish approved RCA)
                           └▶ policy gateway + audit log
```

## RCA workflow (`backend/repolens/agents/workflow.py`)

`planner → gather → rank → (enough evidence? else gather, max 2 loops) → synthesize → report → verify`

* **gather** (scout/cartographer/historian): hybrid chunk search (RRF over vector + lexical, symbol boost), stack-trace
  frame lookup, commits touching suspect files, prior incidents from memory, static callers.
* **rank**: deterministic, auditable scoring → hypotheses with confidence capped at 0.92. It is a heuristic score, not
  a probability; the UI says so.
* **synthesize**: the only LLM step, optional. Offline (`heuristic` provider), over-budget or failing → deterministic
  narrative. Responses are cached by (prompt version, model, repo SHAs, prompt hash).
* **verify**: every cited chunk/commit is re-checked against the index; unverifiable evidence is dropped.
* Root cause stays `unconfirmed` until a human confirms; confirmation promotes the incident to verified memory.

## Facts vs. inference
Evidence carries `provenance` (`retrieval`, `git`, `static_analysis`). LLM text only appears in the narrative.

## Telemetry
Every model/embedding call → `llm_call_events` (tokens, latency, cache hit, cost from versioned pricing, usage source).
Local embeddings are tagged `usage_source=local`; cache savings are tagged `estimated`. HTTP metrics at `/metrics`
(Prometheus), traces via OTLP when `REPOLENS_OTLP_ENDPOINT` is set.

## Knowledge base and graph

Everything searchable lives in one chunk table (`code_chunks`), tagged `chunk_type`: code symbols, **doc sections**
(heading-aware: `parent_symbol` holds the heading path), config/blocks and **pull requests** (`pr`).

Sources: git repositories (docs + code + commits + diffs), **uploaded document collections**
(`Repository.source = "docs"`, full text in `documents`) and GitHub pull requests (`services/pulls.py`).

The graph (`kg_nodes`, `kg_edges`, built by `services/graph.py` after every index) has deterministic node ids, so
re-indexing one repository never orphans edges owned by another.

| Edge | Meaning | Provenance |
|---|---|---|
| `CONTAINS` | file → symbol, class → method, doc → section → subsection | static_analysis / doc_link |
| `IMPORTS`, `CALLS`, `TESTS` | resolved imports, call sites (same file > imported file > repo > org-unique), test→code | static_analysis |
| `LINKS_TO` | doc links `[x](../a.md#h)`, `[[wiki]]` → file/section | doc_link |
| `MENTIONS` | doc/PR text naming a symbol or path (backticks, CamelCase, snake_case) | doc_mention / github |
| `MODIFIES` | commit or PR → file | git / github |
| `INCLUDES` | PR → commit | github |
| `RELATED_TO` | doc section ↔ code/section by embedding similarity (threshold, top-k) | semantic |

Retrieval (`services/knowledge.py`): hybrid search → each hit annotated with its graph connections → results expanded
through high-weight edges (`via_graph`) → `ask` builds numbered sources, answers with an LLM (citations checked) or
falls back to extractive passages when no model/budget is available. The RCA workflow's Cartographer step uses the
same graph to attach documentation, tests and the PR behind a suspect commit as evidence.

## Change-risk scoring (Oracle)
`services/risk.py`: R = 100 · Σ wᵢsᵢ over size 0.15, dependency centrality 0.20, public interfaces 0.10, historical defects
0.20, test gap 0.15, sensitive paths 0.10, similar past changes 0.10 (each sᵢ ∈ [0,1]). Hunks from `git diff -U0` map a
change to the symbols it edits; the knowledge graph supplies dependents (incl. other repositories), tests and docs.
History/similarity signals only use commits older than the assessed one (no look-ahead). Refs are resolved to SHAs before
git sees them. Output always carries evidence, model version and limitations; levels: <25 low, <50 medium, <75 high.

## Access control
`Repository.visibility` is `org` or `restricted`; `repo_access` lists granted users (admins always pass). `scope_for(db,
principal)` returns a `Scope` (None = unrestricted; an empty scope means *nothing*, never "everything"). It is applied at
the lowest practical level: repository lookups, `search_chunks` repo filters, graph queries (nodes whose repository is
out of scope disappear together with every edge touching them, and paths never route through them), investigations
(visible only if every repository in their evidence is in the viewer's scope), memory (entries carry the repositories
they were derived from), risk dependents and dashboard counts. `tests/test_access.py` crawls the read endpoints as a
non-granted user and asserts the restricted repository never appears.

## Evaluation
`repolens/evals` generates seeded synthetic repositories with injected regressions and runs six ablation configurations
plus the Oracle; see `evals/README.md` for results and limits.

## Provider settings
`/api/settings/llm` (admin): provider, model, base URL, API key (Fernet-encrypted, never returned), optional prices that
override the env pricing for cost reporting. Resolution order: organisation setting → `REPOLENS_LLM_*` env defaults.
GitHub: **Connect with GitHub** uses the OAuth *device flow* (`/api/github/oauth/device/{start,poll}`) with an OAuth App
the admin registers (Client ID only, no secret, no redirect URI). The device code stays server-side (`oauth_flows`), the
resulting token is Fernet-encrypted and never returned; a personal access token remains available as a fallback.
`github-models` is an OpenAI-compatible provider (`https://models.github.ai/inference`) that defaults to the connected
GitHub token, so no second key is needed; whether a given OAuth token is entitled to GitHub Models is verified with the
*Test connection* button rather than assumed.

### Why not "Sign in with Anthropic / Copilot"
* **Anthropic** only offers API keys to third-party apps; Claude subscription OAuth tokens are restricted to Claude Code
  and claude.ai under Anthropic's terms, so RepoLens takes an API key (stored encrypted) and never subscription tokens.
* **GitHub Copilot** has no public API for third-party apps. Community clients reuse VS Code's client ID and a private
  token endpoint, which is undocumented and may breach GitHub's terms, so it is deliberately not implemented. GitHub
  Models is the supported way to use GitHub-hosted models.

## Not in the MVP (per the product brief)
Fix generation / Blue-Red-Judge agents, change-risk (Oracle) scoring, GitLab/Bitbucket,
GitHub App installation flow (webhook receiver for `push` re-indexing is implemented), reranker model.

## Scaling notes
Background jobs run in-process thread pools; a restart mid-index leaves a repo in `indexing` (re-index to recover).
For multiple API replicas move to a queue/worker (the job entry points `index_repository` / `run_investigation` are
already session-scoped functions) and RWX storage for clones.
