# Implemented features

Legend: ✅ built and tested · ⚠️ built, tested only with mocks/offline defaults · ❌ not built

## Ingestion
* ✅ Git repositories: local path or https/ssh clone; incremental re-index by content hash; commit + diff history
* ✅ Tree-sitter symbol chunking: Python, JavaScript, TypeScript/TSX, Go, Java; line-window fallback for other files
* ✅ Configuration and deployment files: `Dockerfile*`, `Makefile`, `Jenkinsfile`, Terraform, `.ini/.conf/.properties/.xml/.proto`, YAML/TOML/JSON, `.env.example` (real `.env`, keys and certificates are never indexed)
* ✅ Documentation: Markdown / MDX / RST / text / AsciiDoc, heading-aware sections, front matter, links, `[[wiki]]` links, code mentions
* ✅ Uploaded document collections (no git needed), edit/delete with incremental re-index
* ⚠️ GitHub pull requests (title, body, discussion, files, commits, labels, Jira keys) via PAT or OAuth device flow; webhook re-sync
* ✅ GitHub push webhook re-indexing (HMAC-verified)
* ✅ Private clones use the connected GitHub token via an environment-passed header, only for the host it was issued for; URLs with embedded credentials are rejected
* ❌ GitLab / Bitbucket, GitHub App installation flow

## Retrieval and knowledge graph
* ✅ Hybrid search: pgvector similarity + Postgres full-text + symbol/path matching, reciprocal-rank fusion, deterministic ties, light stemming
* ✅ Stack-trace understanding (Python/JS/Java), innermost-frame priority
* ✅ Evidence links: GitHub/GitLab permalinks for commits, file ranges and PRs in evidence, reports, search and Ask (Jira comments get real link marks)
* ✅ Git blame endpoint and failing-line blame (SZZ-style) as an RCA signal — neutral on the benchmark, see `evals/`
* ✅ Knowledge graph (files, symbols, doc sections, commits, PRs) with typed edges: CONTAINS, IMPORTS, CALLS, TESTS, LINKS_TO, MENTIONS, MODIFIES, INCLUDES, RELATED_TO (semantic) — each with provenance; cross-repository links
* ✅ Graph API + interactive graph view: filters, search, neighbours, shortest path, expand
* ✅ Graph-expanded search and **Ask** with numbered, verified citations; extractive fallback when no model is configured
* ⚠️ Embeddings: local hash embedder by default (offline); OpenAI-compatible provider implemented but not evaluated
* ❌ Reranker model, Neo4j/NetworkX graph store (graph lives in Postgres)

## Investigation (RCA)
* ✅ LangGraph workflow: plan → gather → rank → (retry loop with limits) → synthesize → report → verify
* ✅ Evidence of five kinds: code, commits (with changed lines), docs, tests, callers, PRs — every citation re-checked against the index
* ✅ Hypotheses with a heuristic confidence (capped at 92%, labelled as not a probability); root cause stays *unconfirmed* until a human reviews it
* ✅ Human review → verified incident memory; cached synthesis; budget-aware LLM fallback
* ⚠️ Narrative synthesis with Anthropic / OpenAI-compatible (tested with mocks); the GitHub Models provider is retired by GitHub (30 July 2026)
* ❌ Separate Blue/Red/Judge/Oracle-as-agent roles, fix generation, adversarial review, sandboxed test execution

## Change-risk scoring (Oracle)
* ✅ Interpretable 0–100 score from seven weighted signals (size, dependency centrality, public interfaces, historical defects, test gap, sensitive paths, similar past changes) with evidence, model version and limitations
* ✅ Assess a commit, a branch range before merge, a synced PR, or a raw file list; ranks recent commits; auto-assesses PRs on webhook sync
* ✅ Blast radius: changed symbols → dependents (including other repositories) → tests to run → docs to update
* ❌ Calibration against real outcomes (needs labelled incidents)

## Browse GitHub

Once a token is connected (Settings, a personal access token or the device flow), the **GitHub** page lists every repository it can see (own, collaborator and organisation, most recently pushed first, searchable by name or description, 25 per page, up to 500), marks the ones already in RepoLens, and shows a repository's live pull requests, issues and branches with open/closed/all filters. "Add to RepoLens" clones and indexes it, with a branch picker. Nothing is imported until you add it; a token that cannot see a repository simply does not list it. Rejected or expired tokens produce a clear message and the token is never echoed.

## Browsing Jira and Confluence

**Jira:** the Jira page now has a project list (from the connected account), status, type and text filters that build the JQL for you (still editable under "Edit JQL"), and "Import these as searchable history", which opens Wiki > Sources with the Jira connector pre-filled with that query. **Confluence:** under Wiki > Sources, "Browse spaces instead of pasting a URL" lists the spaces the connected account can see and a space's page tree (titles only, no bodies), with one-click "page", "+ children" and "Import whole space". Hidden spaces stay hidden, and the Confluence side is read-only.

## Bitbucket

Settings > Bitbucket connects **Bitbucket Cloud** (an app password with a username, or a repository / workspace access token) or **Server / Data Center** (a personal access token and the base URL). The **Bitbucket** page works like the GitHub one: searchable repositories (with already-added ones marked), live pull requests with open/closed/all filters, branches, and "Add to RepoLens" with a branch picker. Private repositories are cloned with the credential sent only as a header to the matching host, never inside the URL, and Bitbucket's clone links have the embedded user name stripped. Use a read-only credential. Bitbucket issues are not listed. Cloud repositories are listed per workspace (the account-wide listing was removed from the Bitbucket API): enter your workspace slug in the connection, or give the token the read:workspace scope so they can be discovered. Atlassian API tokens (scoped, used with your email) are supported alongside access tokens and app passwords. Not tested against a real Bitbucket account, only against fake servers for both flavours.

## Single sign-on and SCIM provisioning

**Single sign-on** (configured through the API, `PUT /api/settings/sso`, with no Settings page for it right now) connects any OpenID Connect provider (Okta, Microsoft Entra ID, Google Workspace, Keycloak...): issuer URL, client id/secret, allowed e-mail domains, and the role for new users (viewer or member; SSO never grants admin). The sign-in page shows "Continue with single sign-on" once the typed e-mail's domain has SSO. It uses the authorization-code flow with PKCE; state, nonce and verifier are kept on the server, single use and expire after 10 minutes; ID tokens are verified against the provider's published keys with an algorithm allow-list, plus issuer, audience, expiry and nonce; only verified e-mail addresses in the allowed domains sign in; a user is never moved between organisations and a domain can belong to only one. The browser receives a one-time code, never a token in a URL. Set `REPOLENS_PUBLIC_URL` behind a proxy so the redirect URI returned by `GET /api/settings/sso` is the one you register.

**SCIM 2.0** (token via `POST /api/settings/scim/token`; endpoints `/scim/v2`, Users, filter by userName/externalId, PATCH, DELETE) lets the provider create, update and deactivate accounts with a per-organisation bearer token (shown once, only its hash is stored, rotatable). Deactivation ends sessions immediately and blocks sign-in; history is kept. Role values from the provider are honoured (including admin, since the provider is the source of truth for provisioning).

Not tested against a real Okta/Entra tenant: the flows are covered by a fake provider that enforces PKCE, signatures and nonces.

## Wiki proposals: edits you approve

From a drift alert, **Draft an update** creates a proposal for that page; from a confirmed investigation, **Draft a runbook** creates a new runbook page (Symptoms, Diagnosis, Fix, Verification, Prevention, References). With an AI model connected the draft is written by the model from the page, the changed code and recent commits; implausible output (a page shrunk to a line) is discarded. Without one, an update only adds a clearly marked review note and a runbook is a structured template, and the proposal says so. Proposals show a colour diff, can be edited first, and are written only when a person approves. A page edited after the draft was written is refused. Approving writes to the collection and re-indexes; for pages synced from Confluence the only action is a **comment** on the Confluence page (admin only, needs comment permission), so the page itself is never overwritten. Pages inside a code repository cannot be written, so the proposal is for copying into a pull request.

## Documentation drift alerts

After a repository is re-indexed, the lines that changed are mapped to the functions, classes and files they touch, and every page the knowledge graph links to them (a section mentioning the symbol) is flagged on **Wiki > Drift**, with a count badge in the sidebar. Pages changed in the same commit as the code are not flagged. One alert per page accumulates changed symbols across commits; it closes by itself when the page is edited, or you can mark it reviewed. Alerts are visible only to people who can see both the code repository and the page. Detection is a hint, not a proof: it only knows pages that name the code by identifier or file path.

## Sources: Jira, Slack and scheduled sync

Wiki > Sources connects more than documents. **Jira tickets** (a JQL query; uses the Jira connection from Settings) become one page per issue with fields, description and the last 20 comments, so "has this broken before?" is answered from past incidents. **Slack channels** (bot token with `channels:history`, `channels:read`, `users:read`; channels listed by name) turn threads with replies into pages and busy days into digests, with mentions and links resolved; a Slack workspace export can be uploaded instead, with no token. **Notion** (internal integration token; every page shared with it, or a starting page and its sub-pages; headings, lists, tables, code and links are converted), **Google Docs** (a Drive folder shared with a service account; Docs and .md/.txt files, subfolders kept as paths; the key is encrypted and the token endpoint is fixed, never read from the key file) and **SharePoint / OneDrive** (Microsoft Graph app-only; .md, .txt, .html and .docx from a library folder) work the same way. Imports are incremental (changed items rewritten, deleted ones removed), redact secrets before storing, default to restricted visibility, and are admin-only. Each connected source can be re-synced on a schedule (hourly to weekly) or manually; a failing source retries on its schedule instead of hammering the service. Tokens are encrypted and never returned. Items synced from a source cannot be deleted one by one, because they would return on the next sync: remove the source.

## Investigations from CI failures

Investigations > **From a CI log**: upload or paste a JUnit XML report, pytest, Jest or Go test output, or any log with stack traces. The failures are listed (duplicates merged, with their file and line), you choose up to ten, and each becomes its own investigation. Secrets in the log are redacted before storing or sending to a model, XML with a DOCTYPE is rejected, and logs over 2 MB are refused. The server parses the log again when you start, so the browser cannot inject a different query.

## Ownership: who to ask

Ownership is derived from indexed commits: each author scores per file from the size of their changes (log-scaled) with a six-month half-life, so current maintainers outrank whoever wrote it years ago; bots are ignored. The **Ownership** page shows each top-level area's owner split, a bus factor (people holding 80% of the knowledge) and flags areas with a single owner or an owner inactive for a year. Any file or folder can be looked up, the file viewer shows who maintains the open file, and every root-cause report adds a "Who to ask" section for the files it points at. It is a hint from commit history, not a measure of anyone's work.

## Language model providers

Settings > AI & search: **Anthropic Claude**, **Google Gemini**, any **OpenAI-compatible** service (OpenAI, Azure, vLLM, Ollama, Groq, OpenRouter...), or offline. Gemini uses Google's OpenAI-compatible endpoint (`generativelanguage.googleapis.com/v1beta/openai`), which is fixed so the key only ever goes to Google; create a key at aistudio.google.com/apikey. Its free tier is rate limited and Google may use free-tier requests to improve its products, so avoid it for private code. Model names are free text. **Test connection** then lists the models the saved key can actually use (for Gemini, Anthropic and OpenAI-compatible services; Gemini's list is limited to chat-capable models), flags a configured model that is not on the list, and lets you click one to use it. A 404 on chat now says the model was not found instead of a raw HTTP error. Gemini embeddings can be used too through the embedding settings (`EMBEDDING_BASE_URL` set to the same endpoint), untested.

## Multiple models and routing

Settings → AI & search → **Models and routing** lets you connect several models, even from different providers (Gemini, Claude, any OpenAI-compatible endpoint). After **Test connection** you tick the models to use and give each a tier: *light* (quick, cheap), *standard* or *strong* (hard problems). Tiers are guessed from the model name.

Each task picks a tier from what it needs. Answering a question and connection tests start light; root-cause write-ups, doc updates and runbooks start standard. Signals such as a long prompt, a stack trace, a lot of evidence, several repositories or low confidence push a task up a tier. **Savings mode** (on by default) sends plain tasks one tier down. When the monthly budget is nearly used, everything goes to the cheapest tier.

If a model fails, the next one in the chain answers. If a model returns an answer that cannot be used (for example malformed JSON), one stronger model is tried. A single configured model is used for everything, exactly as before. The routing plan on the page shows which model each kind of task uses right now, and every answer records which model produced it.

## Answer memory

Answers are remembered on the server. A repeated or re-worded question is matched by local embedding similarity (92% by default), and is answered from memory with no model call. The answer shows **From memory · saved ~N tokens** and **Answer again with the model** to bypass it.

An entry is only reused while the repositories and wiki pages it was based on are unchanged, so a re-index or edit retires it. Entries are keyed by the repositories the asker can see, so answers never cross access boundaries, and expire after 72 hours. **Knowledge → Memory** lists everything remembered, with a search that can also read the answers ("deep"), reuse counts and tokens saved; admins can forget single answers or clear all, and any selection can be exported as a Claude skill (`SKILL.md`, secrets removed). Members see only answers made for repositories they can access. Settings keeps the on/off status and a link. Tune with `REPOLENS_ANSWER_MEMORY_ENABLED`, `_THRESHOLD`, `_TTL_HOURS` and `_MAX_ENTRIES`.

## DeepMind Wiki: search, edit and atlas

- **Search by meaning** across every page from the Browse tab (`GET /api/wiki/search`), one result per page with a snippet. Results respect repository access.
- **Edit and create pages** in documentation collections without leaving the wiki (`PUT /api/wiki/page`). Saving redacts secrets, re-indexes and relinks the page to code. Pages synced from Confluence stay read-only here.
- **Code atlas** (a tab per code repository): modules with file, class and function counts, their most connected symbols, and how much of each is explained by the wiki, so undocumented areas are easy to spot.

## Asked before, and fewer tokens per answer

When you ask, RepoLens first shows **Asked before**: earlier answers to similar questions (embedding similarity or the same content words, so re-wordings match). This is free and instant, and the model's answer follows. A close enough match (92% similar, or 80% of content words) is reused as the answer. Answers whose sources changed are never suggested.

To cut tokens further, the context sent to a model is condensed to the lines that match the question, repeated passages are dropped, and savings mode uses a smaller budget. A light model answers first; if its answer cites nothing and does not say the sources lack the answer, one stronger model retries.

## Why there is no Redis

The answer memory, shared agent digests and exact-prompt cache live in the application database (SQLite in development, PostgreSQL in production), next to the data they describe. That keeps them durable across restarts, lets them be invalidated in the same transaction as a re-index, and means one fewer service to run. A small in-process cache holds recent question embeddings. Redis becomes worthwhile only when several API servers need to share a very hot cache or you need sub-millisecond lookups at high request rates; the lookup functions are isolated in `services/answer_memory.py` for that day.

## Agents share context

When an investigation finishes it publishes a short digest (problem, root cause, fix, evidence references). The runbook writer reads that digest instead of the whole evidence again, so the same facts are not paid for twice. Digests expire after 30 days.

## Jira, profile and insights

- **Jira**: open a ticket to read its description and comments, run an analysis (error signatures, stack frames, files and symbols, what was tried, proposed cause, workarounds, fixes, unanswered questions, gaps) and start an investigation seeded with those signals.
- **Profile**: display name, password, and every device you are signed in on, each of which can be ended. Signing out ends the session on the server.
- **Insights**: AI utilisation per model and day, tokens avoided by routing and memory, work done, what is indexed and who is active.
- **Documents**: pages show their kind, purpose, warnings, owners, commands, endpoints and settings at a glance; long sections split at paragraph boundaries and never cut a code block or table.

## Investigation reliability

- A run stops after `REPOLENS_INVESTIGATION_TIMEOUT_S` (300 s by default) and is marked failed with the reason. Runs left "running" by a restart are failed by a startup sweep. A failed investigation shows why and has **Run again**.
- Errors such as `KeyError: 'name'`, `'NoneType' object has no attribute`, `IndexError` and divide-by-zero are read against the retrieved code: the report names the line that raises it and the code that builds the value without it, with no model needed.
- Publishing to Jira asks for the issue key (taken from the title when present) and says when publishing is switched off.

## Accounts and security

- Sign-in is a server-side session: list and end your devices, sign out everywhere else, idle sessions end after 8 hours, a password change signs out other devices.
- Five wrong passwords for one account from one address pause sign-in for ten minutes.
- Adding a model needs a passing live test with its key; keys are encrypted and never returned.

## Search quality: embeddings and reranking

- **Embeddings:** the default is an offline hash embedder (no network, deterministic). Set `REPOLENS_EMBEDDING_PROVIDER=openai` with `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` and `EMBEDDING_API_KEY` to use any OpenAI-compatible embedding service, including code-specific models. Set `EMBEDDING_SEND_DIMENSIONS=false` for providers that reject the `dimensions` field. `EMBEDDING_DIM` must match the model, and a re-index is needed after changing it.
- **Reranking:** after hybrid search fuses vectors and keywords, the top `RERANK_DEPTH` (30) candidates are re-scored. `lexical` (default) is offline: BM25 over the candidates, query-term proximity and identifier matches, blended with the fused score. `api` calls any Cohere/Jina/Voyage-style `/rerank` endpoint (`RERANK_BASE_URL`, `RERANK_MODEL`, `RERANK_API_KEY`) and falls back to the fused order if the service is down. `none` turns it off.
- Settings shows what is active (read-only). The benchmark has configurations with and without the reranker; see `evals/README.md` for what it does and does not show.

## Branches, clones and disk

- **Branches:** when adding a repository, "Choose branches" lists the remote's branches (`git ls-remote`, no clone). Each selected branch becomes its own index (`name`, `name@branch`) with its own graph, risk scores and investigations, while all of them share a single clone. Up to `REPOLENS_MAX_BRANCHES` (8).
- **Lean clones:** git clones are blob-less and have no working tree (`REPOLENS_CLONE_FILTER=blob:none`; set empty for full clones). History stays complete for blame, log and risk; file contents are downloaded in one batch at index time, and only for files that will be indexed. Servers that do not support partial clone fall back to a full clone.
- **Cleanup:** clones unused for `REPOLENS_CHECKOUT_TTL_DAYS` (30; 0 disables) are deleted by a background job; indexes are kept and the clone is re-created on the next re-index. Admins see sizes and can preview or run a cleanup under Repositories -> Clone storage. Deleting a repository removes its clone unless another branch still uses it. Only directories under `REPOLENS_REPOS_DIR` are ever deleted.
- Local-path repositories are read in place and are never cloned or deleted.

## Graph: connections and heat map

- Arrowheads show direction; hovering or selecting a node labels its links (calls, imports, mentions...). Relationship types can be toggled on and off, and the side panel summarises a node's connections by kind.
- **Colour by** switches nodes from type colours to a heat map: recent change (lines changed in 90 days), bug-fix activity (commits whose message says fix/bug/hotfix/revert/regression/crash/patch), most connected, or public code no document mentions. Churn and bug heat apply to files and are inherited by the symbols inside them; nodes without data are grey.

## DeepMind Wiki

A browsable, connected knowledge base over everything that is documentation.

- **Sources**: README/Markdown/txt/rst uploads or pasted text, public web URLs (GitHub blob links become raw links), and **Confluence** (Cloud: email + API token; Data Center: personal access token) by page, page tree or whole space. Credentials are Fernet-encrypted, never returned by the API, and never placed in URLs. Imports default to *restricted* visibility; only admins connect or import.
- **Incremental sync**: pages are re-fetched only when their Confluence version changes; deleted pages are removed; one click re-sync per source.
- **Browse**: page tree mirroring Confluence hierarchy, breadcrumbs, outline, backlinks, outgoing links, related pages (embedding similarity), code each page describes (links into the code graph), and concepts. `[[wiki links]]` and relative links resolve in-app.
- **Ask**: question answering restricted to documentation, with cited sources and graph connections.
- **Concepts**: terms recurring across pages, with definition, mentions, and related code.
- **Health**: orphan pages, broken links, stale pages, near-duplicates, undocumented public code, outdated references (docs naming code that no longer exists).
- Everything respects per-repository access control.

Caveat: the Confluence flow is tested against an in-repo fake server (`python -m repolens.devtools.fake_confluence`), not a live Confluence instance.

## Integrations
* ⚠️ Jira Cloud (email + API token) and Data Center (PAT): read issues/JQL, investigate tickets, publish an approved RCA comment (ADF); SSRF-guarded
* ⚠️ Jira field updates / transitions ❌
* ⚠️ GitHub OAuth device-flow sign-in (own OAuth App) (GitHub Models, which it used to unlock, was retired by GitHub on 30 July 2026)
* ✅ Anthropic by API key (Claude subscription sign-in is not available to third-party apps); ❌ Copilot (no public API)

## Usage, cost and memory
* ✅ Per-call events: tokens, cached tokens, latency, cache hit, versioned pricing, usage source (provider-reported / estimated / local)
* ✅ Dashboard: totals, trend vs previous period, by model / agent / workflow, cost per successful RCA, tokens avoided (estimate)
* ✅ Daily/monthly hard budgets (stop optional inference); per-organisation pricing override
* ✅ Incident memory + exact cache with version-aware keys
* ❌ Budget settings UI, per-model/per-workflow limits and alerts, repository summaries, semantic cache, small/large model routing

## Platform
* ✅ FastAPI backend, Vite + React + TypeScript + Tailwind frontend (Dashboard, Knowledge, Change Risk, Repositories, Investigations, Jira, Token Usage, Settings)
* ✅ JWT auth, roles (admin/member/viewer), multi-organisation isolation, policy gateway (deny-by-default) with audit log, Fernet-encrypted secrets, secret redaction in prompts
* ✅ **Per-repository access control**: restricted repositories are hidden (with their code, history, graph nodes/edges, search hits, Ask sources, investigations, memory, risk dependents and dashboard counts) from users who are not granted access; admins manage grants; revoking takes effect immediately
* ✅ **Secret redaction at ingestion**: API keys, tokens, private keys, JWTs, URL credentials and quoted secret literals are removed from stored chunks, commit diffs, uploaded docs, PR text and the file viewer, with a per-run counter
* ✅ Alembic migrations (0001–0005), Docker images, Compose (+Jaeger profile), Kubernetes manifests (default-deny network policy, non-root), GitHub Actions CI/CD
* ✅ Prometheus metrics, OpenTelemetry tracing, structured logs
* ✅ Evaluation harness with a synthetic benchmark (see `evals/`)
* ❌ CLI, isolated code-execution sandbox, CI/PR-triggered analysis beyond the webhook, retention/deletion policies, rate limiting on login
* ⚠️ Access control has no per-repo *write* levels (roles still decide who can change things) and aggregate numbers such as a risk signal's "N dependents" may count hidden dependents

## Settings

Two tabs. **Integrations** works like a connector catalogue (each connected row also shows the account the credential belongs to, such as @gnsaddy or a display name, recorded when you save and refreshed by Test): a **Connected** list on top (icon, name, status, what it is connected to, with Test, Configure and Disconnect) and **Available integrations** below, each with a **Connect** button that opens a card for the credentials, with a **Test connection** button that checks what you typed before anything is saved. GitHub (a personal access token, or the device flow), Bitbucket, Jira and Confluence are connected here. Slack, Notion, Google Docs and SharePoint are set up per collection in Wiki > Sources, so their Connect button goes there, and they appear under Connected with a source count once you have one. **AI & search** shows the language model like any other integration: when connected it lists the provider, the **model in use** and where the key comes from, with Test, Change model and Disconnect. Setting it up is a guided card with no model choice up front: paste a key and **Test connection** (nothing is saved), then the models that key can use appear to pick from (or type a name for services that cannot list them), **Test** the chosen model, and only then **Save**. Search quality is a read-only status below it.
