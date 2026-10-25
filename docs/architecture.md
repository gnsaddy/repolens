# Architecture

Modular monolith: one FastAPI app, in-process background workers, PostgreSQL + pgvector (SQLite in development).

```
Vite/React SPA ──/api──▶ FastAPI ──▶ LangGraph RCA workflow ──▶ retrieval (pgvector + FTS + symbols + git history)
                           │                                   └▶ LLM adapter (optional) ─▶ usage events
                           ├▶ indexer (git → tree-sitter chunks → embeddings, incremental by content hash)
                           ├▶ Jira / Confluence / Bitbucket / GitHub / Slack / Notion connectors
                           ├▶ model router ─▶ providers (Anthropic, Gemini, OpenAI-compatible) ─▶ usage events
                           ├▶ answer memory + agent digests (cache table)
                           ├▶ wiki (document intelligence, atlas, proposals, drift)
                           └▶ sessions + policy gateway + audit log
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

## Model registry and routing
`services/llm_router.py`, `services/llm.py`. An organisation can connect several providers (`llm_connections`, keys
Fernet-encrypted) and several models per provider (`llm_models`, each with a tier: light, standard or strong, an enabled flag
and optional prices). A model is saved only after it answered a live test on the server. Each call carries a `Task`
(kind, prompt size, evidence count, stack trace, repositories, retrieval confidence). `decide()` starts from the kind's
default tier, moves up one tier when the task scores two or more complexity points, moves down one tier in *savings mode*
when it scores none, and uses the cheapest tier when the monthly budget is nearly spent. The result is an ordered chain: the
wanted tier by price, then the nearest tiers preferring stronger. `LLMClient.complete` walks the chain on provider errors and,
when a `validate` callback rejects an answer, retries once with the next stronger model. One model means no routing, so the
original single-model behaviour is unchanged. Every call is a usage event with its own model and price.

## Answer memory and shared context
`services/answer_memory.py`. A model-written answer is stored (`answer_memory`) with the question's embedding, the answer
and sources, and a *fingerprint* of the indexed sources it relied on (repository ids, indexed commit, chunk and file counts,
document content hashes). Lookup matches a new question by embedding cosine (default 92%) or by overlap of content words
(80%), only within the same access scope key and fingerprint, and only inside the time to live, so re-indexing or editing a
page retires the entry. `similar()` returns near matches for the UI to show before the model answers. `browse()` lists and
searches entries (deep search also reads the answer text); `build_skill()` packages a selection as a `SKILL.md` with secrets
redacted. Retrieved context is condensed before it is sent (`services/context.py`: duplicates dropped, lines that match the
question kept), and a finished investigation publishes a digest (`services/agent_context.py`) that the runbook writer reads
instead of the whole evidence. Memory and digests live in the application database rather than Redis (see features).

## Investigation lifecycle
`services/investigations.py`. Runs execute in a thread pool. A run is stopped when it exceeds
`REPOLENS_INVESTIGATION_TIMEOUT_S` (checked between workflow steps) and marked failed; a background sweep in `main.py` also
fails runs left unfinished by a restart (the queue is in memory) or a hung call, and `POST /investigations/{id}/rerun` starts a
failed one again. `services/cause_hints.py` reads KeyError, None/undefined attribute, IndexError and ZeroDivisionError
messages against the retrieved code to name the exact lines and the producer that lacks the value, with no model; when a model
is connected the finding is added to its prompt. `services/jira_analysis.py` distils a ticket's description and comments
(signals, what was tried, proposed cause, workarounds, fixes, open questions, gaps) and seeds the investigation query.

## DeepMind Wiki internals
`services/docs.py` splits Markdown, RST and text by heading, then splits long sections at paragraph boundaries (never inside a
fenced block or a table, repeating the last plain paragraph for context). `services/doc_intel.py` classifies each page
(runbook, incident, decision record, API reference, design, how-to, changelog, README) from its name, title and body and extracts
purpose, warnings, owners, commands, endpoints and settings. `services/wiki.py` builds the tree, page view, concept index,
health report, search (`/wiki/search`) and the per-repository atlas (modules, most connected symbols, documentation coverage).
Pages in documentation collections can be created and edited in place (`PUT /wiki/page`); synced pages stay read-only.

## Accounts and sessions
Every sign-in creates a `user_sessions` row and the token carries its id (`sid`). `current_principal` rejects a token whose
session was revoked or has been idle past `REPOLENS_SESSION_IDLE_MINUTES`. People list and end their sessions, sign out
everywhere else, and change their password (which ends the other sessions) under `/api/auth`. `services/ratelimit.py` pauses
sign-in after repeated failures per account and address. `/api/metrics/overview` aggregates content, work done, AI use,
savings and activity for the Insights page.

## Not implemented
Fix generation / Blue-Red-Judge agents and a generated-code execution sandbox, a GitHub App installation flow (the webhook
receiver for `push` re-indexing is implemented), multi-factor authentication.

## Scaling notes
Background jobs run in-process thread pools. A restart mid-index leaves a repository in `indexing` (re-index to recover);
unfinished investigations are failed by the startup sweep and can be run again. For multiple API replicas move to a
queue/worker (the job entry points `index_repository` / `run_investigation` are already session-scoped functions), put a gateway
rate limit in front of sign-in, and use RWX storage for clones. Move the answer memory to a shared cache only if several
servers need one very hot cache; its lookup functions are isolated in `services/answer_memory.py`.
