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

**Roles.** `policy.ACTION_MIN_ROLE` maps each action to the lowest role that may do it (`user.list` is admin: the list holds everyone's
email, role and sign-in method, and only the repository Access panel needs it). A handler calls `authorize(db, p, action, ...)`, which
raises 403 and writes an audit event for allowed and denied calls. `tests/test_role_matrix.py` builds a schema-valid request for every
operation from the OpenAPI document and calls it as viewer, member and admin on a real workspace. A 2xx counts as allowed (a 404 or 422
can come before the check). It compares the lowest successful role with the one the code declares (`authorize` actions, `require_role`),
fails on any server error, and pins the lists of reads that are open to viewers, member-only and admin-only. A second test uses real
objects (an investigation, a saved question, a skill, a collection, a user) to confirm a viewer or member gets 403 and nothing changes.
`test_every_mutating_operation_runs_a_policy_check` now discovers every module in `repolens/api` instead of a hand-written list. About 30
operations validate the request or look up the object before they check the role, so a lower role gets 404 or 422 for an id that does
not exist and 403 for one that does; ids are random, so this is not a way in.

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

## How an answer is built
One function, `knowledge.retrieve_checked` (`retrieve` is the same without the report), decides what an answer is built from. Ask chat
and `knowledge.ask`, which the Knowledge page, the DeepMind Wiki, MCP and the full quality runs call, all use it, so a retrieval check
tests exactly what an answer sees. It runs the hybrid search, then
**joined names**: code often writes two words as one (`ratelimit.py` for "rate limiting", `signin` for "sign-in"), which a search on the words
never finds because prose that spells it out outranks it. `walkthrough.joined_pairs` joins neighbouring words of the question, and
`joined_terms` keeps only the joined names that occur in a file path or symbol name in the index. They get their own search; the chunks
whose file or symbol name contains the name (up to three) and their direct neighbours in the code graph, who calls them (up to two), are
woven in after the best match (`knowledge._joined_hits`, `_with_joined`). With no joined name in the index nothing extra runs. Then
`services/walkthrough.py` classifies the question (`task_kind`: *lookup*, *explain* or *build*). For explain and build questions
`route_sources` adds the routes the question is about (the `routes` chunks written at index time by `services/routes.py`), each
followed by its handler and the functions the handler calls, permission checks first. Routes are scored by their path (twice) plus
the question's words in the handler body. Equal scores are ordered by how often the handler uses those words (`_mentions`), and the
top `MAX_ROUTES` are taken from different files first (`_spread`, one per file, then the best of the rest), so one module of
near-identical routes, such as a SCIM copy of the user routes, cannot fill every slot. The route sources go ahead of the ordinary
results (`merge`). Then it measures **coverage** (a joined name found in a source covers both of its words): the share of the question's key words (`walkthrough.key_words`, stemmed the same way as
the text) that the six best sources mention. Below 60% it searches **once more**, wider and with the missing words in other forms
(assign: assigns, assigned, assigning), and keeps whichever pass covers more. No model is called, so the same question always gets the
same sources, and the response says whether it searched again (`retrieval`: coverage, missing words, refined). If the sources still
miss most of the question the chat and Knowledge page say so. The prompt then gets the add-on for the kind of question (walkthrough steps, or build-from-conventions), and
the best one or two approved **skills** (`services/skills.py`, table `skills`, migration 0024): guidance in a marked block that says it cannot override the
citation, secret or untrusted-source rules. Matching skills use their own words, not retrieval's. If the model call fails, the
reason is reported (`describe_failure`), never a list of loose passages passed off as an answer.

After the model answers, `services/name_check.py` looks for **names that do not exist**: file paths, routes and functions the answer
states outside code blocks. A name is fine if it appears in the sources or the question, or anywhere in the indexed repositories
(one lookup each against `code_chunks`, within the person's access); only a name found nowhere is reported (`unsupported_names`, an amber
"Check these names" badge in the chat). Build requests are skipped, since they write new names on purpose. No model is called.

Saved answers are keyed on `answer_memory.PIPELINE`, the access scope and the skills used (`skills.fingerprint`), so changing how
answers are produced (bump `PIPELINE`) or adding a skill never replays an older answer; build requests are never replayed.

**Quality checks** (`services/golden.py`, tables `golden_questions` and `golden_runs`, migration 0025). A saved question lists files a
good answer must be built from (a path fragment is enough) and words it must contain. A *retrieval* run calls `retrieve` and checks
the files, needs no model and is free; a *full* run calls `ask` with the memory bypassed, also checks the words, and is limited to
20 questions (`MAX_FULL`). Every run stores a fingerprint of the index, the skills, `PIPELINE` and the search model, so a drop in the
pass rate comes with which of those changed since the previous run.

*Automatic runs* (migration 0026 adds `golden_runs.trigger`: `manual` or `reindex`). `api/repos._run_index` calls `golden.after_index(org)` when an
index completes. Repositories often finish together, so each call replaces a pending `threading.Timer` for that organisation
(`auto_quality_delay_s`, default 120 s) and only the last one fires; with inline jobs (tests) it runs at once. `auto_run` then does nothing
unless the workspace has saved questions, no repository is still `indexing` (that index will call `after_index` itself), and the fingerprint
differs from the last completed run's, so an index that found nothing new changes no answer and costs nothing. It starts a free retrieval run
as user `""` (unrestricted scope, like a webhook; the run is visible to admins) and logs a warning with the count when questions that passed
before now fail. It never calls the model; `auto_quality_checks=false` turns it off.

**Independent review** of an investigation is off by default and switched by an admin (`llm_settings.independent_review`, read by
`llm_router.review_enabled`). It is advisory: its result is recorded in `verification.review` and shown in the report, and it never
changes the confidence or the ranking.

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

## Suggested fixes
`services/fixes.py` builds a fix from a confirmed investigation: it reads the evidence files at the indexed commit, asks the `fix-writer`
task (strong tier of the model router, strict JSON of `{path, old, new}` replacements) or falls back to a template over
`cause_hints`, applies the replacements (each `old` must occur exactly once; forbidden paths are refused), computes the unified diff
with secrets masked, checks Python syntax with `ast.parse`, and stores a `FixProposal` (migration 0020). `api/fixes.py` lists, reads,
serves the patch and records the decision; approval re-reads the files from the default branch through the GitHub write helpers,
re-applies the edits and opens a draft pull request, or fails without writing anything if they no longer apply. Before writing,
`GitHubClient.is_public` reads `GET /repos/{repo}`; the fix view exposes it as `target_public` (null when unknown), and the decision
endpoint answers `409` for a public target unless the body has `confirm_public: true`. `pr_report.build(public=True)` is the strict
variant of the pull request comment (score, signal names and the pull request's own files only), chosen by the Action's `public` flag.

## Chat
`ChatThread` and `ChatMessage` (migration 0021) hold a person's conversations; every endpoint in `api/chat.py` looks a thread up by id **and owner** and answers 404 otherwise. `services/chat.py` answers a message: it builds a search query from the question, the errors and code names found in an attached log (`error_lines`, `extract_stack_refs`) and, for a short follow-up, the previous question, searches with the asker's repository scope (`knowledge.search`, the same retrieval as Ask), and builds the numbered sources with `knowledge.sources_from`. With a model, the prompt has SOURCES, the ATTACHED FILE (head and tail), the last six turns and the QUESTION, under a system prompt that calls all of that untrusted data; the reply must cite sources unless fallback is on, unknown citations are flagged, and a model failure falls back to the extracted answer. The model is not called when there are no sources, no attachment and fallback is off. Editing a question deletes the later messages and answers again. Sources are stored with each answer and filtered by the viewer's current scope when a chat is read.

## How a document, such as a PDF, becomes knowledge
1. **Upload and extraction.** A PDF is read page by page with `pypdf` (`services/importers.py`); only pages with selectable text count, a scan with no text layer is skipped, and nothing reads text out of pictures. Each page becomes a `## Page N` section of one Markdown document with a short "Imported from" note. Word, Notion and CSV files take the same route.
2. **Keeping the text literal.** PDF and Word text is prose or code, not Markdown, so it is escaped as it is written: `[`, `]`, `*`, backticks and `~` are escaped, and a line that begins with `#`, `>`, a dash, a number and a dot, or a row of dashes is escaped, so a code listing like `tensor([[1, 14350]])` is never read as a link to a page called "1, 14350" and a `# comment` line is never a heading. The link detectors also ignore escaped brackets. Only our own `## Page N` markers (and Word's real headings and lists) are structure.
3. **Storing.** The Markdown is masked for secrets (`redact_stored`) and saved as a `Document` row in the collection (`path`, `content`, a content hash). The original file is not kept, only this text. Uploading the same file again changes nothing, and a changed file replaces the text.
4. **Splitting.** The indexer (`services/docs.py`) cuts the text at headings, so a PDF becomes one section per page. A section longer than 80 lines is cut at paragraph boundaries into parts of about 60 lines with an 8-line overlap, and code blocks and tables are never cut in the middle. Each part is a chunk with its heading path, line range, the code names it mentions and the links it contains.
5. **Making it searchable.** Each chunk gets a vector (the local `local-hash-v1` embedder: words and word pairs hashed into 256 numbers, with no model involved) computed from its file path, heading and text (first 6,000 characters), and a full-text entry. On PostgreSQL the vectors sit in pgvector with an HNSW index.
6. **Connecting.** The graph builder adds a `doc` node and a `section` node per chunk, `CONTAINS` edges, `LINKS_TO` for links between documents, `MENTIONS` where a section names a function, class or file in a repository, and `RELATED_TO` edges between sections and code whose vectors are close (similarity at least 0.30, at most 3 per section; skipped for very large collections).
7. **Asking.** A question is turned into terms and a vector. Search runs two ways, by vector similarity (cosine, at least 0.15) and by keyword, and the two ranked lists are merged by reciprocal-rank fusion with small boosts for a name or path match. The top 30 are re-scored lexically (BM25 and how close the question's words sit to each other), and the best eight become the sources, each with its file, heading and lines, plus the graph connections around them.
8. **Answering.** With **no model**, the answer is the most relevant passages, labelled as extracted and not generated, each cited as `[S1]`, `[S2]`. With a model, the sources are condensed to a character budget and sent with the question, the model must cite `[S#]`, a citation to a source that does not exist is flagged, and repeated questions are answered from memory while the sources are unchanged.

Limits that follow from this: the built-in vector captures word overlap, not meaning, so a question worded very differently from the page ranks lower (measured: 42% first-place on re-worded questions against 96% on exact ones; a real embedding model helps, see `evals/README.md`); text extraction loses layout, so tables and figures in a PDF may come out as plain lines or not at all; headings in a PDF are not recognised (only page boundaries are), so sections are pages; and a scanned PDF has no text to read.

## Keeping indexes fresh
`services/index_jobs.py`. `api/repos._run_index` hands each finished run to `after_run`. A `failed` run whose error can pass by itself
(`retryable`: anything that does not match `PERMANENT`, such as a wrong path, branch or credentials) schedules a daemon `threading.Timer` for the
next delay in `index_retry_delays` (default 60 s, then 300 s), with at most one pending retry per repository, reset on success. When it fires it
indexes again only if the repository is still in the error state. `main._auto_reindexer` runs `daily_pass` every `auto_reindex_hours` (24, first
pass 15 minutes after start-up). `changed()` is true for a repository whose last index failed, that never finished, that has said "indexing" for
over three hours (the server stopped), or whose branch head differs from `indexed_sha` (`gitops.remote_head`, a `git ls-remote`, for clones; the
local branch head for local paths). One that cannot be reached is left for the next pass. The reason for a failure is `IndexRun.error`, attached
to the repository response as `last_error` (redacted, 300 characters) only while it is in the error state. The retention sweeper
(`_retention_sweeper`) is started from the same lifespan; it had been written but never started, so the daily clean-up only ever ran from its
manual endpoint.

## Watching RepoLens itself
`telemetry.py`. Besides HTTP request count and latency, `/metrics` (Prometheus text; bearer token when `REPOLENS_METRICS_TOKEN` is set) exposes:
`repolens_index_runs_total{status}`, `repolens_index_seconds` (histogram), `repolens_index_retries_total`, `repolens_answers_total{mode,cached}` and
`repolens_answer_seconds{mode}` (counted in `knowledge.ask`, so Ask, chat and full quality runs all show), `repolens_llm_tokens_total{kind}` (from
`usage.record_event`; local embeddings spend none), `repolens_quality_runs_total{trigger,status}`, `repolens_quality_pass_ratio{org}`,
`repolens_repositories{status}` (counted from the database when scraped, not kept up to date by writers), and per background loop
`repolens_job_last_success_timestamp_seconds{job}` and `repolens_job_failures_total{job}`.

Each loop started in `main.lifespan` registers itself (`register_job(name, every_s, first_s)`) and reports each pass (`job_ok` / `job_failed`).
`job_report()` classifies a loop as `ok`; `waiting` (still inside its first delay); `failing` (the last pass raised); or `late` (no pass in
two intervals plus a minute, or never ran past its first delay plus that). Registering up front is the point: the retention sweeper once
never started and nothing showed it, whereas now it would show as `late`. `GET /api/metrics/health` (admin) returns the loops, repository counts
by status, the last completed quality run and a list of plain-language problems; Insights shows it as "RepoLens health". A useful alert is
`time() - repolens_job_last_success_timestamp_seconds > 2 * <interval>`, or `increase(repolens_job_failures_total[1h]) > 0`.

## Systems: repositories analysed together
`services/groups.py`, `api/groups.py`, tables `repo_groups` and `group_overviews` (migration 0027), page `pages/Graph.tsx` with `SystemGraph.tsx`.
A group is a saved list of up to 15 repository ids with a name, a description and `created_by`; it is shared in the workspace. Nothing about the
picture is stored: `analyse(db, org, ids, days)` computes it when asked, from data that is already indexed.

* **Links.** `_links` joins `kg_edges` to `kg_nodes` twice (source and destination) and keeps the edges whose two ends are in different chosen
  repositories, except `CONTAINS`. They are aggregated per ordered pair (count, summed weight, count by relation) with the best `trust`
  (`graph_insights.trust`: fact, inferred or uncertain) and up to three example edges per pair, strongest first, each with its evidence string.
  Cross-repository edges are created by `graph.build_repo_graph` (a call to a name that exists in exactly one other repository, document mentions,
  semantic similarity), so a repository must be indexed after the others it uses for the arrow to appear; the daily re-index repairs that.
* **Strong, URL and name-only.** (The URL and import finders below now run as stored detectors, see "Stored dependencies between repositories".) A cross-repository edge below weight 0.8 is a call matched by name alone (`graph.build_repo_graph` adds one when
  a name exists in exactly one other repository); it is counted as `weak`, the pair's trust stays `uncertain`, and it is left out of the code graph
  unless `weak=true`. `_http_links` adds a third kind that is not stored in the graph: for every function, method or class chunk, quoted strings are
  searched for URL paths and compared with the route inventory (`routes.parse` of each repository's `routes` chunks) by full path and by tail, so a
  caller that leaves out a gateway prefix still matches. A route counts only with two fixed words of three or more letters (or one of eight), and files
  under docs, examples, tutorials, scripts or tests are skipped on both sides. The result is cached per set of repositories and index versions. The
  edge runs from the function holding the URL to the route's handler (`rel` `HTTP`, trust `inferred`, weight 0.7).
* **Imports.** `_import_links` is the strongest proof and is not stored in the graph either. `_packages` lists what each repository offers: the top
  directories of its Python packages (a directory with `__init__.py` whose parent has none, outside docs, examples and tests) and each `package.json`
  `name`. Every function, method and class chunk of the other repositories carries its file's import statements (`CodeChunk.imports`, kept on
  definitions only) and the names it calls (`calls`); `parse_imports` reads those and keeps the ones that name another repository's package. For
  `from userlib import get_user` the `_Resolver` looks for `get_user` defined in `userlib/__init__.py`, and when that file only re-exports it
  (`from .core import get_user as get_user`, read from the file's own text and import lines) follows the re-export up to three files deep to the
  definition (`fact`). A name the package takes from a third package (`from starlette.responses import Response`) is dropped, since it is not defined in
  the repository; one found only somewhere under the package directory is `inferred`. An edge runs from the function that calls the name to the
  definition (`rel` `USES`), or from the file when only the import exists (`IMPORTS`); `import userlib` then `userlib.get_user()` links through the call
  name and is `inferred`. At most four calling functions are drawn per definition and the rest are counted in the first edge's evidence. A proved edge
  replaces a name-matched `CALLS` edge between the same two nodes. In `_links` imports count in `imports` and make the pair `fact`.
* **Code graph.** `GET /api/groups/code-graph` returns what the knowledge-graph view draws (`node_dict`, `edge_dict`): the nodes at both ends of up
  to 60 cross-repository links (imports first, then HTTP, then calls the graph states), the file or class that contains each, and up to two callers or callees in the same
  repository, at most 150 nodes. `src` and `dst` narrow it to one direction of one pair. The page draws it with `ForceGraph`, which already puts
  each repository in its own area and bridges between them.
* **Change together.** `_coupling` reads commits in the window: a ticket key (`[A-Z][A-Z0-9]+-\d+`) in messages of two repositories is weighted three,
  the same author changing two repositories on one day is weighted one; bot authors (`ownership.BOT`) are skipped.
* **Heat.** `_risk` counts commits, changed lines, fix commits (the same words `/api/graph/heat` uses) and open drift alerts per repository;
  `_heat` scales each to 0..1 on a log scale across the chosen repositories.
* **Overview.** `facts()` renders the analysis as plain lines; `generate` shows the model only those lines (`SYSTEM` forbids other names), flags
  paths, routes and functions in the answer that are not in the facts (`unsupported`, a string comparison, no database), and stores the result in
  `group_overviews` with `created_by`, the model, and the repository ids it was built from. A failed model call raises 502 with the reason and
  stores nothing; with no model the facts are stored under a label saying so. The newest ten per system are kept.
* **Access.** A group's response lists only repositories in the caller's `Scope` and counts the rest; analysis runs on the visible ones only; an
  overview is returned only when `scope.can_all(overview.repo_ids)`, re-checked on every read like investigations. Unknown and invisible
  repository ids are the same 404. Writes go through `authorize` (`group.write`, `group.overview`, both member); changing or deleting a group needs
  its author or an admin. `purge_repository` calls `forget_repo`, which removes the repository from its groups (deleting a group left empty) and
  deletes overviews written from it. `POST /api/groups/{id}/overview` is in the heavy-call rate limit.

## Stored dependencies between repositories
`services/repo_links.py`, tables `repo_links` and `link_states` (migration 0028). The finders that used to run each time the Graph page was opened now live here
as **detectors** (`Detector`: `name`, `kind`, `languages`, `detect(db, ids) -> (links, coverage info)`), registered in `DETECTORS`. Today there are two,
`ImportDetector` (imports, resolved to definitions through a package's re-exports) and `HttpDetector` (URLs matched to routes); a new kind of link is a new
detector, with nothing else to change. `refresh(db, org)` runs them over every repository of the workspace when the fingerprint (each repository's
`indexed_sha`) differs from `link_states.fingerprint`, under one lock per workspace, and then stores the result: a link seen before keeps its `first_seen_at`,
one that is no longer found gets `gone_at` (kept 180 days as "removed"), a link that comes back counts as new again. A detector that raises is logged and its
kind is left alone, so one broken finder never makes the others' links look removed. `link_states.baseline_at` is set when a workspace is first looked at;
links that existed then are never reported as new. `after_index` (called from `_run_index`, debounced per workspace by `auto_link_delay_s`) refreshes after an
index, so a new dependency is noticed with nobody watching; the page also refreshes lazily when the fingerprint is stale. Settings: `auto_link_refresh`,
`auto_link_delay_s`. `DETECTION_VERSION` is part of the fingerprint: change it whenever a detector finds links differently, and every workspace works its links out again. Metrics: `repolens_repo_links{kind}` and `repolens_repo_links_new_total`.

* **Asking about a system** (`groups.ask`, `POST /api/groups/ask`). It calls the ordinary `knowledge.ask` with the chosen repository ids (so retrieval is limited
  to them and to the caller's `Scope`) and three extra parameters: `extra`, `memory_tag` and `addon`. `extra` are pseudo-results of kind `analysis` built by
  `system_facts` (the fit-together overview, the stored proof lines ranked by overlap with the question's key words or limited to one pair, the impact of one
  node, and the coupling), appended after the retrieved ones so they are numbered, cited and name-checked like any other source; `context.blocks` keeps
  them whole instead of condensing them to the query terms. `memory_tag` is the link-state fingerprint plus the focus, so a saved answer is never reused
  after the dependencies change. `addon` tells the model what the analysis sources are and not to claim a dependency no source states.
  `retrieval_focus` limits retrieval to code and pull requests (documents too when the question names them) and passes `skip_paths` so a locale folder
  under a docs directory is dropped, unless the question is not ASCII. The action
  `group.ask` is open to viewers and rate limited with the other model-backed calls.
* **Manifests** (`services/manifests.py`, `ManifestDetector`). Pure parsers, one per file type, each returning what the file provides and what it requires with the line of
  each (`provides`, `requires`, `error`). `tomllib` reads TOML and `json` reads JSON; XML (`pom.xml`, `.csproj`) is read with patterns, never an XML library, because
  entity expansion in a hostile file could exhaust memory (a test feeds it an entity bomb). A file that cannot be read gives an error in the coverage, never an
  exception. `_manifest_texts` rebuilds each manifest from its chunks line by line (skipping tests, examples and vendored folders). Names are compared after
  `manifests.norm` (for Python `User_Lib`, `user-lib` and `user.lib` are one package). A requirement that names a package another repository provides becomes a
  `repo_links` row (`kind` `manifest`, `rel` `DECLARES`, `tier` `declared`, trust `fact`) between the two manifest files, with several requirements from one file to one
  file counted in `more`. `analyse` compares declarations with use: `declared_unused` (declared, no import, URL or stated call) and `undeclared` (used, not declared, and
  only when the first repository has a manifest we read and the second offers a package). `surface` lists who declares a repository's package (`declared_by`) and no
  longer counts a manifest file as a class or function. The indexer now keeps `go.mod`, `Gemfile`, `.csproj`, `.kts` and `.gemspec` (`parsing.SPECIAL_FILES`,
  `LANG_BY_EXT`); an existing index gains them on its next index.
* **Shared names** (`services/identifiers.py`, `IdentifierDetector`). `identifiers.extract(path, text)` reads one file by its kind (`classify`: Dockerfile, CI,
  Compose, YAML for Kubernetes/Helm/Argo, Terraform, shell, `.gitmodules`, example env files, other config) and returns `Ident`s (`kind`, normalised `value`,
  `role` defines or refers, `line`). Everything is line and pattern based, never a YAML library (an anchor bomb would expand), and only names are kept, never an
  environment variable's value or a URL's credentials. `norm_repo` makes every spelling of a repository address one `host/owner/name`, `norm_image` drops tag,
  digest and registry alias and refuses base images and templates, `host_of` refuses `localhost` and the git hosts. The detector reads config and text chunks of
  each repository, adds each repository's own address (`url`, `web_url`) as a definition, and reads application code (function, method and class chunks) with
  `code_refs` only for names some repository defines (environment-variable reads, URLs to defined hosts, string literals equal to a defined queue or topic).
  `_join` links a reference in one repository to a definition in another (never the same one), skips a name defined by more than `MAX_SHARED_DEFINERS` (3)
  repositories, keeps `MAX_PAIRS_PER_KIND` (6) file pairs per pair of repositories and kind and counts the rest in `more`, and writes one `repo_links` row per file pair with
  `kind` set to the kind of name (`repo_ref`, `image`, `host`, `queue`, `env`; `Detector.kinds` lists several so a failure leaves all of them alone). `IDENT_MAP`
  gives each kind its trust and its relation: `REFERENCES` and `DEPLOYS` are fact, `CONNECTS` and `USES_QUEUE` inferred, `CONFIGURED` uncertain (counted as weak, drawn
  only with name matches). A link to a whole repository ends at an anchor file (README, manifest, else its first file). Evidence passes through `redact_stored`.
  `_coverage` adds each repository's `role` (`infrastructure` when five or more of its non-test files are pipelines, manifests, Terraform or scripts and at least
  twice the code files). `surface` lists who points at a repository by name (`linked_by`).
* **Coverage.** Each detector says which languages it reads (`languages`); `link_states.coverage` records, per repository, the languages found (function, method
  and class chunks), the ones a detector cannot read (`unread`), the packages it provides and its route files. The Graph page prints "Not read: go in
  orders-svc" so an empty graph is not mistaken for "no dependencies".
* **Blast radius** (`impact`): the stored links whose destination is the node (or, for a file, anything defined in it), limited to repositories in the
  caller's `Scope` (the rest are only counted), the top two recent authors of each calling file (`Change.files`, bots skipped), and in-repository callers one step
  further in (`CALLS` edges with weight 0.5 or more, three per function).
* **Public surface** (`surface`): links grouped by destination, with the number of uses (links plus `more`), and, for Python packages, the names the package
  `__init__.py` re-exports or lists in `__all__` that no stored link points at. A name unused here may be used outside the workspace, and the page says so.
* **What changed** (`changes`): new links (`first_seen_at` in the window and after `baseline_at`) and removed ones (`gone_at` in the window), grouped by
  repository pair with up to three examples and their proof lines.

## Graph insights
**Worlds.** `ForceGraph.layout` groups nodes by repository (`repoGroups`). With two or more, `placeWorlds` puts the repositories on a wide ring and
relaxes their centres: they never overlap, and repositories with links between them are pulled to just beyond touching, in proportion to the number
of links. Each node is pulled to its own repository's centre, and a link across repositories is a longer, weaker spring. The drawing adds a faint
region and name per repository and draws cross-repository links as amber dashed bridges. One repository uses the original single-cluster layout. Which nodes reach the browser is decided by `GET /api/graph/overview`: for one repository its `limit`
most-connected nodes; for all of them a budget of `limit` per repository (at most 400) shared out with a window function (`row_number() over (partition by
repo_id order by degree desc)`), every repository getting `budget / repositories` of it and the most-connected of the rest filling any room a small one leaves.

`services/graph_insights.py` derives three things from the stored graph, with no model call. **Trust** maps each edge's provenance to fact
(static analysis, document link, git, GitHub), inferred (semantic, document mention) or uncertain (the same with weight under 0.5);
`edge_dict` carries it. **Communities** come from deterministic label propagation over one repository's edges (nodes visited in sorted order,
ties to the smaller label, groups under three left out, numbered by size), labelled by their most common directory. **Hubs** are the highest-degree
code and document nodes; **surprising connections** are the rarest links between two communities (never CONTAINS or MODIFIES). The result is
cached per repository until it is indexed again and rendered as the Start here report (`GET /api/graph/report`, `/api/graph/communities`, and a
`community` field on node dicts). The MCP server exposes `graph_overview`, `graph_neighbors` and `graph_path`.

## Real-bug evaluation, calibration and injection tests
`agents/workflow.py` ranks suspects with one entry per file, down-weights commits that share no words with the report and context files (docs, tests, changelogs) unless a stack trace names them. `evals/suites/bugs.py` mines fix commits from a local repository, snapshots the repository at each parent commit (a shared clone whose
only reachable history ends there), indexes it with the offline stack and scores retrieval and the investigation shortlist against the
files the fix changed, with Wilson intervals and a random-ranking comparison. `services/calibration.py` compares investigation
confidence with confirmed/rejected reviews (bands, Brier, ECE, pool-adjacent-violators curve once 20 reviews exist) behind
`GET /api/dashboard/calibration`. `tests/test_injection.py` is a red-team suite: hostile text in documents and code must stay on the
data side of every prompt, planted secrets must not reach a prompt, a model that obeyed the attacker cannot edit build or deploy files,
add command execution or network calls to a fix, or flood a report, and the frontend has a guard test against raw-HTML rendering.

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
