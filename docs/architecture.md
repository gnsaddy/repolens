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

`services/jira_analysis.compare` finds what several tickets share (errors, files, symbols, components) and flags likely duplicates
(similar summaries, or the same error in the same file); `api/jira.py` serves the batch endpoints (at most ten tickets, each ticket
fetched once). The UI shares one ticket browser (`JiraIssueBrowser`) between the Jira page and the Investigations picker.

`services/dashboard.py` builds the dashboard's attention list, 14-day trend, AI summary, knowledge snapshot and activity feed from existing
tables (served by `GET /api/dashboard/insights`, slower than the headline `GET /api/dashboard`, so the UI polls it less often).

Night mode is `html.dark` (`lib/theme.ts`): `index.css` re-points the colour variables Tailwind's utilities read (canvas, surface,
ink, the slate scale and the tinted badge colours), restores the light values inside the sidebar, and lets `bg-white` follow the
surface colour. The preference lives in `localStorage` and the device setting is followed while it is "system".

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

`services/totp.py` implements RFC 6238; `services/account_mfa.py` accepts a code or a recovery code at sign-in; `services/auth_tokens.py`
issues and redeems invitation and reset links (`auth_tokens`, hash only); `api/account.py` holds People, invitations, resets and the
authenticator endpoints; `api/audit.py` serves the admin audit log.

`services/retention.py` is the daily sweep (background task in `main.py`, also `POST /api/settings/retention/run`);
`devtools/delete_workspace.py` removes one workspace and everything that references it.

`services/mcp_server.py` is a small Model Context Protocol server (initialize, ping, tools/list, tools/call, batches, notifications)
whose tools call the existing endpoint functions in-process with the caller's `Principal`, so permissions, repository scope and
audit are the same as in the web app. `services/api_tokens.py` and `ApiToken` back personal access tokens (`rlp_` prefix, hash only);
`current_principal` maps a read token to the viewer role and a write token to at most member.

`services/pr_report.py` turns a change (file names and counts) into the comment Markdown using `risk.assess`, `ownership.experts` and the
graph; `integrations/github-action/review.py` (standard library only) reads the PR's files from GitHub, calls
`POST /api/risk/pr-report` with a read token and keeps one sticky comment up to date.

## Evaluation suites, feedback and server metrics
`repolens/evals/suites/` scores the daily-use paths against fixed data: `docs` indexes a 24-page corpus into a throwaway database
and measures retrieval by question style, `memory` scores the two matching signals of the answer memory on labelled pairs and
sweeps thresholds, `routing` replays task shapes through `decide()` against three priced virtual models. Embedders are
pluggable (`--embedder http` for any OpenAI-compatible endpoint). `evals/baselines/suites.json` is the contract the CI gate enforces.
`feedback_events` stores thumbs up/down with the (redacted) question and a few flags; `GET /api/feedback/summary` feeds Insights.
`services/embedding_state.py` records the embedding model per workspace after the first index and compares it with the
configuration. `services/sysmetrics.py` samples /proc every five seconds (CPU, memory, network) and the request guard records
request rate and latency into the same ring buffer for `GET /api/metrics/system`.

`services/outbound.py` builds the "what leaves this server" list from the stored connections and the server settings; it never reads
a secret and a destination on localhost is reported as staying on the machine.

## Browser tests
`frontend/e2e` (Playwright) starts `uvicorn` on a throwaway SQLite database (port 18000) and the Vite server proxying to it (port
15173), registers a workspace through the API and drives the UI in a real browser. They guard what unit tests cannot: that a page
renders with the real API's answers. Run with `make e2e`; CI runs them in a separate job.

## Not implemented
Fix generation / Blue-Red-Judge agents and a generated-code execution sandbox, a GitHub App installation flow (the webhook
receiver for `push` re-indexing is implemented), multi-factor authentication.

## Measured limits (single PostgreSQL 16 + pgvector 0.8, 256-dimension vectors, 200,000 chunks, one 8-core machine)
Vector table 261 MB plus HNSW index 260 MB plus full-text index 39 MB. Nearest-neighbour search: 4.6 ms unfiltered, about 40 ms
when filtered by workspace and repository (the planner scans exactly, recall 100%), full-text OR query about 120 ms. Building the
HNSW index took 7.7 minutes single-threaded (parallel build needs more shared memory than a default container has), so for a very
large first import load the rows first and build the index afterwards. The local hash embedder produces about 5,700 chunks per
second (a million chunks in under four minutes); a model behind an HTTP API is limited by that API. SQLite (development) reads
every chunk into Python, so use PostgreSQL beyond a few tens of thousands of chunks.

## Scaling notes
Background jobs run in-process thread pools. A restart mid-index leaves a repository in `indexing` (re-index to recover);
unfinished investigations are failed by the startup sweep and can be run again. For multiple API replicas move to a
queue/worker (the job entry points `index_repository` / `run_investigation` are already session-scoped functions), put a gateway
rate limit in front of sign-in, and use RWX storage for clones. Move the answer memory to a shared cache only if several
servers need one very hot cache; its lookup functions are isolated in `services/answer_memory.py`.
