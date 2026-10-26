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

Each task picks a tier from what it needs. Answering a question and connection tests start light; root-cause write-ups, doc updates and runbooks start standard; suggested code fixes start on the strongest tier. Signals such as a long prompt, a stack trace, a lot of evidence, several repositories or low confidence push a task up a tier. **Savings mode** (on by default) sends plain tasks one tier down. When the monthly budget is nearly used, everything goes to the cheapest tier.

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

## Pull request reviews (GitHub Action)

Add the RepoLens Action to a repository and every pull request gets one comment, updated as the PR changes: the change-risk score and its top reasons, who knows the code, the tests that cover it, what else uses it, and the documentation that describes it. It sends RepoLens only file names and line counts, and the comment stays within that repository's own information unless you ask otherwise. Setup is in `docs/github-action.md`.

## Use RepoLens from your editor (MCP) and access tokens

Create a token under **Settings → MCP & tokens** (read, or read and start investigations; it expires and can be revoked) and add RepoLens to your editor: `claude mcp add --transport http repolens https://<your-server>/api/mcp --header "Authorization: Bearer rlp_…"` for Claude Code, a `url` + `headers` entry for Cursor, or the standard input/output bridge for Claude Desktop (the page shows each snippet with your token filled in). The editor then has these tools: `search`, `ask`, `find_symbol`, `symbol_brief`, `read_wiki_page`, `who_to_ask`, `change_risk`, `get_investigation`, and `investigate` (write tokens only). Answers cite files and lines; nothing is sent anywhere except to your own RepoLens server, which only uses the language model you connected.

## Suggested fixes (with your approval)

On a confirmed investigation, **Propose a fix** drafts a small code change for the root cause: the exact lines to replace, a diff you can read, the tests worth adding, and a syntax check. It is limited to three files and a short change, never touches build, deployment, dependency or secrets files, and is never run. You can download it as a patch, reject it, or approve it, which creates a branch and opens a **draft** pull request on GitHub for someone to review. RepoLens never merges, and refuses if the files changed since the fix was drafted. With a connected language model it writes the change itself; without one it only offers a plain template for simple cases (a missing dictionary key) and otherwise says it cannot help.

**Public repositories.** When the fix is shown, RepoLens asks GitHub whether the target repository is public. If it is, the fix carries a red "public repository" badge, and approving shows a prominent warning: the branch, the code change and the explanation would be visible to everyone. The dialog's button stays disabled until you tick a specific acknowledgement, and only then does the app send `confirm_public: true`. The server enforces this too: approving a fix for a public repository without `confirm_public` is refused with `409` and nothing is written to GitHub. Private repositories behave as before; if GitHub cannot tell (`target_public` is null), there is no extra step. Rejecting never needs confirmation.

## Is the confidence score trustworthy?

**Insights → Can the confidence score be trusted?** shows, for investigations people confirmed or rejected, how often each score band was actually right, the average calibration error and a plain verdict (overconfident, underconfident, about right). With 20 or more reviews it fits a correction; with fewer it tells you how many are missing. Confirming or rejecting root causes is what feeds it.

## Long pages in the wiki

A wiki page with more than 12 sections (an imported PDF has one per page) opens **one section at a time**, with Previous, Next, a picker and a "Show all N sections" link, instead of loading everything at once. The "On this page" list becomes the list of sections (the first 40, then "Show all"), highlights the one you are on and jumps between them. Showing all of a very large page (over 150 sections) asks first. Shorter pages look exactly as before.

## Reading a document in the Knowledge page

In Knowledge → Documents, **Read** (or the document's name) opens the document in a reader instead of the small list. The reader has a contents list of its sections (for a PDF, one per page), shows a long document one section at a time with Previous and Next, or all of it together; **Find in this document** lists every section that contains a word with a snippet and jumps to it; **A− / A+** changes the text size (14 to 22 px); **Full screen** gives it the whole window (Esc leaves); **Download** saves the Markdown; and **Open in wiki** goes to the same page in the DeepMind Wiki. How the text was made and searched is described in `docs/architecture.md` ("How a document, such as a PDF, becomes knowledge").

## Importing documents from your laptop

Knowledge → Documents → **Upload files** accepts, besides Markdown and text (`.md .mdx .rst .txt .adoc`):

* **Word** (`.docx`): headings, lists and tables become Markdown.
* **Notion**: in Notion choose Export → Markdown & CSV, then upload the `.zip`. The ids Notion adds to every name are removed, links between pages are kept working, databases (CSV) become pages with a table, and images and attachments are left out. A zip may hold a zip (Notion splits big exports); any other zip of Markdown, text or CSV files imports the same way.
* **CSV**: becomes a page with a table (first 300 rows).
* **PDF**: the text of a PDF that has selectable text, one section per page (read with the `pypdf` library, which is part of the install). **No OCR**: a scanned PDF (pictures of pages) has no text to read, and is skipped with a clear explanation.

Nothing needs a model, and nothing leaves the server. **Large documents get a warning, never a refusal:** a PDF over 100 pages, text of over about 300,000 characters, a zip that adds more than 200 pages, or anything cut at the import limit is imported, and the result explains that long documents add many sections that can crowd search results and the wiki's health counts, and that documents up to about 100 pages work best. After an upload a short result says how many documents were imported, how many files were skipped, and why. A file that cannot be read never stops the others. Limits: text files 1 MB; Word, PDF, CSV and zip files **25 MB each**; up to 20 files and 100 MB in one upload; a zip up to 1,500 files and 60 MB unpacked; a PDF's first 400 pages.

## Security, FAQ and About pages

The public pages now also include **FAQ** (`/faq`, "Questions about trust": is it safe, does it send my code anywhere, do I need a model, does it train on my data, who builds it, what are the official channels, is it production-ready, what if the AI is wrong, can I delete my data, how big a repository, what does it cost, how to report a problem), and the Security page opens with **Where each control stands**: every control marked Built, Partly or Not yet, including the gaps (no SAML, no database encryption of our own, no export of everything, no penetration test, no SOC 2 or ISO 27001). The footer groups Product, Trust, Developers and Community links and states the audit position. A day/night switch is on the sign-in page, the reset page and every public page, and the choice is remembered and shared with the app. The statements live in `frontend/src/lib/trust.ts`; change them only with the code that backs them.

## Security and About pages

**Security** (`/security`) and **About** (`/about`) are public pages, readable without signing in and linked from the sign-in screen, the sidebar footer and Settings → About. Security gives a table of everything that can leave the server and when, plain statements on access control, secrets, the AI's limits and the audit log, how to check the claims yourself, honest limits (beta, no independent audit or certification, you run it), a private way to report a vulnerability, and the official source. About says what RepoLens is, what it does, how it is meant to behave, where it stands, who built it and how to get involved.

## Sign-in page

Beside the sign-in form, a panel says what RepoLens does (ask with citations, catch stale pages, see how things connect, find a root cause) and why it can be trusted: data stays on your server, people approve outward actions, access controls and an audit log, secrets masked, open source with no phone-home, and a plain statement that this is a beta without an independent audit or certification. It links to `SECURITY.md` and the source. A small flowing-graph illustration uses the same motion as the graph page and stops for people who ask their system for less motion. On narrow screens a one-line version sits under the form. `SECURITY.md` lists where each claim is backed.

## Graph trust, areas and Start here

Every connection in the graph says how far it can be trusted: **fact** (found directly in the code, a document link, git or GitHub), **inferred** (guessed from shared words or meaning) or **uncertain** (a weak guess). The graph draws facts solid, inferred links dashed and uncertain ones dotted, and **Facts only** hides the rest. **Colour by → Area** groups nodes into communities, the parts of a repository that hang together (found from the connections themselves, without a model).

**Start here** appears on the graph page and at the top of the wiki's Code atlas once a repository is chosen: the areas, the **hubs** (most connected, so a change there reaches the most other things), **surprising connections** between areas, and good first questions. Editors get the same through three MCP tools: `graph_overview`, `graph_neighbors` and `graph_path`.

## Graph view controls

The graph has a toolbar: **zoom in and out** (the point under the pointer stays put), **fit to screen**, **actual size**, a **zoom level** readout, a **labels** switch (key labels, all labels or none), **download as SVG** and **enlarge** for a taller canvas. The mouse wheel zooms toward the pointer, a double click zooms in, dragging pans, and with the graph focused `+`, `-`, `0` and the arrow keys zoom, fit and pan. Picking a node from the search box moves the view to it. **Animation:** the links of the node you hover or select light up with dashes and small particles that flow outward to what it connects to, and the selected node gently pulses; a highlighted path flows along its links too. The play/pause button in the toolbar turns it off, it stays off when your system is set to reduce motion, and it is left out of the SVG download.

**Full screen** (top right of the graph page) hides the app's own sidebar and summary tiles and gives the graph the whole window, with the node details in a side card that **Hide details** folds away; **Esc** or **Exit full screen** returns to the page. When coloured by area, a legend lists the areas; click one to look at it on its own and dim the rest.

## Logo and About

The mark is a lens over a small graph of connected repository nodes (one node in amber). It is the browser-tab icon (`frontend/public/favicon.svg`), the sidebar and sign-in logo (`components/Logo.tsx`, the same drawing), and appears in **Settings → About** with the version, the licence and links to report a bug or see the source.

## Report a bug or request a feature

The bottom of the sidebar has **Report a bug / request a feature**. It opens the project's public GitHub issue form in a new tab; nothing from your workspace is sent for you, and the dialog reminds people not to paste private code or secrets. Builds for a fork or a company copy can point it at their own tracker with `VITE_ISSUES_URL`.

## Dashboard

The dashboard opens with **Needs attention** (failed indexing or investigations, possibly outdated wiki pages, edits waiting for review, unconfirmed or low-confidence investigations), each line linking to where to act. Below the headline numbers it shows investigations per day, AI use and savings with how helpful people found answers, and a knowledge-base snapshot; admins also see recent activity from the audit trail.

## Night mode

Use the **Night mode** button at the bottom of the sidebar, or pick "Match my device", "Day" or "Night" on your Profile. The default follows your device; your choice is kept in this browser. Colours come from the same design tokens in both modes (see `frontend/src/index.css`), so new screens get night mode without extra work as long as they use the existing colour classes.

## Jira: find, analyse and investigate tickets

The Jira page lists tickets of every type (filter by project, type, status, assignee and text; 25 to 100 at a time, newest update first). Open a ticket to read it with its comments and run an analysis. Tick up to ten tickets and **Analyse together** to see what they share (errors, files, symbols, components), which look like duplicates, and what each says about its cause, or **Investigate all** to start one investigation per ticket. A ticket that already has an investigation keeps it. The Investigations page has **Start from Jira** (only when Jira is connected) with the same search and actions. Publishing a report back to Jira is still a separate, approved step.

## People, invitations and authenticator sign-in

Admins open **Settings → People** to see everyone in the workspace and how they sign in. **Invite someone** creates the account and a one-time link the person uses to choose their own password; **Reset password** makes a fresh link for someone who forgot theirs (the old password keeps working until the link is used, and using it signs out their other devices). Links are handed over by you, nothing is sent by e-mail.

Anyone can add an **authenticator app** on their Profile (optional): after the password, sign-in asks for a six-digit code, with eight recovery codes shown once for a lost phone. Admins can remove a lost authenticator. Turn off open registration with `REPOLENS_ALLOW_REGISTRATION=false` once your workspace exists; people then join by invitation.

## Audit log

Admins get an **Audit log** page: every sensitive action (who, what, on which target), everything that was refused with the reason, and failed sign-ins. Filter by area, outcome, person or text, page through it, and download a CSV. Secrets are masked, spreadsheet formulas are neutralised in the export, and reading the log is recorded too.

## What leaves this server

Settings → AI & search starts with a panel listing every service this server sends data to: each connected model provider (and which models), an embedding or reranking service if one is configured, GitHub, Bitbucket, Jira (and whether publishing is on), Confluence, other wiki sources, a single sign-on provider and a trace collector. For each it shows the host, what is sent, whether it stays on the machine (a model server on localhost does), and where to manage it. A fresh install says "Nothing leaves". The default install connects nothing: no model, the local embedder, no connectors.

## Quality checks and feedback

- **Evaluation suites** (`python -m repolens.evals.suites`, `make eval`): document question answering, answer-memory matching and model routing, scored offline against a recorded baseline. CI fails when a metric gets worse. See `evals/README.md` for the suites and the embedder comparison.
- **Was this helpful?** Every answer has thumbs up and down (a thumbs-down asks what was wrong). Insights shows the share of helpful answers, and admins can export rated questions (`GET /api/feedback/export`) to grow the evaluation sets.
- **Embedding changes are noticed:** the server remembers which model made the stored vectors; Settings warns when the configuration no longer matches, and `python -m repolens.devtools.reembed` prepares the database for the new model.
- **Server load** (Insights, admins): CPU, memory, network and API request rate and latency, live.

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
