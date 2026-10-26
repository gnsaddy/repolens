# Security

Report vulnerabilities privately to the maintainers; do not open public issues for them. Use GitHub's private reporting (the repository's Security tab, **Report a vulnerability**; the maintainer must have it switched on), or contact the maintainer through their GitHub profile. The same guidance is on the app's public **Security** page (`/security`, readable without signing in, linked from the sign-in screen).

Controls implemented (see `docs/architecture.md` for the threat model summary):

- JWT auth with bcrypt password hashing; roles `admin | member | viewer` re-read from the database on every request.
- Every query is scoped by `org_id`; cross-tenant lookups return 404.
- Repository-level access (`services/access.py`): a `restricted` repository is visible only to admins and granted users. The
  same `Scope` object filters repository lists, search, Ask, the knowledge graph (nodes, edges, paths), investigations,
  incident memory, risk assessments and dashboard counts; hidden items answer 404, never 403. Background jobs run with
  their creator's scope, re-evaluated at run time.
- Stored text is scrubbed of high-confidence secrets (`redact_stored`) at ingestion; real `.env`/key/certificate files are
  never indexed. Repository URLs may not embed credentials; private clones use the connected GitHub token via an
  environment-passed header and only for its own host.
- Policy gateway (`services/policy.py`): deny-by-default, role check, per-action policy, audit event for every decision
  (including denials). Jira publishing additionally needs explicit approval, `allow_publish`, a completed investigation
  and passing citation verification.
- Jira tokens are Fernet-encrypted at rest and never returned by the API; the Jira URL must be https and not a
  private address outside `dev` (SSRF guard).
- Confluence: API token / PAT Fernet-encrypted, never returned; base URL goes through the same SSRF guard; imported pages are redacted for secrets before indexing, default to `restricted` visibility, and only admins can connect or import. Web-URL import accepts public https hosts only (redirects re-checked, 1 MB cap). Wiki Markdown strips `javascript:`/`data:` links.
- Single sign-on (OIDC, authorization code + PKCE): server-side single-use state/nonce/verifier, signature and claim verification with an algorithm allow-list, verified e-mail and domain allow-list, no cross-organisation linking, no admin from SSO, one-time exchange code instead of tokens in URLs. SCIM tokens are stored as hashes; deactivated users lose access immediately.
- GitHub OAuth device flow: device codes never leave the server, flows are bound to the admin and organisation that
  started them and expire; tokens are encrypted at rest and never returned.
- Secrets are redacted from text before it reaches an LLM prompt.
- The file viewer only serves paths present in the index (no path traversal); `git` runs read-only with a scrubbed env.
- Retrieved code and Jira text are treated as untrusted: the synthesis prompt instructs the model to ignore embedded
  instructions, and ranking/citation checks are deterministic, not LLM-driven.
- Containers run as non-root with read-only root FS (k8s), dropped capabilities and default-deny NetworkPolicies.

- Sessions: every sign-in is a server-side session carried in the token. People can list and end their sessions, sign-out
  ends the session on the server, a password change ends all other sessions, and a session unused for 8 hours
  (`REPOLENS_SESSION_IDLE_MINUTES`) ends by itself.
- Sign-in rate limiting: five wrong passwords for one account from one address pause sign-in for ten minutes
  (`REPOLENS_LOGIN_MAX_FAILURES`, `REPOLENS_LOGIN_WINDOW_SECONDS`); responses do not reveal whether the account exists.
- Model provider keys (Anthropic, Gemini, OpenAI-compatible), Bitbucket, Jira, Confluence and other connector credentials
  are Fernet-encrypted at rest and never returned; provider error text is scrubbed of the key before it reaches the browser.
  Adding a model requires a live test with the key on the server.
- Answer memory is keyed by the repositories the asker can see and is retired when the indexed sources change, so a remembered
  answer is never served to someone with less access. Browsing it shows members only answers made for their own scope; exported
  skills are redacted for secrets.
- Investigations have a time limit and a sweep that fails stuck runs, so a hung provider call cannot hold work forever.

- Edge protections (`backend/repolens/middleware.py`, applied to every request before routing): security headers on every API
  answer (`nosniff`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, no referrer, `Cache-Control: no-store`); request bodies over
  32 MB are refused (declared and streamed size); rate limits per address (600/min overall, 20/min for sign-in, registration and
  SSO discovery), per session (1200/min) and a tighter one for model-backed and indexing calls (30/min), answered with 429 and
  `Retry-After`. Health checks are exempt. All limits are settings (`REPOLENS_RATE_LIMIT_*`, `REPOLENS_MAX_BODY_MB`).
- The client address cannot be chosen by the client: `REPOLENS_TRUSTED_PROXY_HOPS` says how many proxies in front of the API
  append to `X-Forwarded-For`, and the address is read from the matching position from the right. With 0 (default) the header is
  ignored. Kubernetes sets 2 (ingress + web).
- Server limits: uvicorn drops idle keep-alive connections after 10 s, caps concurrent requests at 200 and limits header size; nginx
  drops slow headers/bodies (15 s / 30 s), caps bodies at 33 MB, hides its version, and sends a strict Content-Security-Policy
  (scripts only from the app itself, no plugins, no framing, forms and base URI locked), HSTS and a permissions policy.
- Passwords: at least 8 characters, at most 72 bytes, not on a list of common passwords, not just repeated characters, and not
  containing the e-mail name; enforced on registration, user creation and password change. Failed sign-ins for a known account are
  written to the audit log (never the password). In production the signing secret must be at least 32 characters.
- `/docs`, `/redoc` and `/openapi.json` exist only when `REPOLENS_ENV=dev`. `/metrics` can require a bearer token
  (`REPOLENS_METRICS_TOKEN`); in Kubernetes it is not reachable from outside the namespace anyway.
- The UI only turns `http(s)` addresses into links (`safeHref`), renders Markdown without raw HTML, and loads pages on demand.
- Admins can read the audit trail in the app (Audit log), filtered and exportable; it covers allowed and refused actions and failed sign-ins.
- Optional authenticator-app sign-in (RFC 6238, one use per 30-second step, encrypted secret, hashed one-time recovery codes); invitations and password resets are one-time links stored as hashes, expire, replace each other and end the person's other sessions; open registration can be closed so people join by invitation.
- Records are not kept forever: a daily retention sweep removes old feedback, audit events, ended sessions and used links (periods are settings), and a workspace can be deleted completely with one command.
- Personal access tokens (`rlp_…`) are shown once, stored as hashes, expire, can be revoked, are capped below admin (read = viewer, write = at most member), cannot mint tokens or change account security, and every MCP tool call is audited by tool name only.
- Suggested fixes are drafts: only confirmed investigations qualify, evidence and code are treated as untrusted text in the prompt, edits must match existing text exactly once, build/deploy/dependency/secret files are refused, nothing is executed, diffs have secrets masked, and a person must approve before a branch and a *draft* pull request are created (the saved GitHub token needs write access for this; RepoLens never merges).
- Prompt injection is treated as a structural risk: every prompt tells the model that documents, code and tickets are untrusted data, hostile text is quoted only in the data part, secrets are masked before indexing and again before sending, model output is parsed strictly and capped, made-up citations are flagged, and a drafted fix is dropped if it adds command execution or network calls or touches build/deploy/dependency/secret files. `tests/test_injection.py` and a frontend guard (no raw HTML, unsafe links dropped) keep this true; they check the structure, they cannot prove a model will never be fooled, which is why a person approves every outward action.
- Nothing private goes to a public place without an informed human decision. Before a draft pull request is opened, RepoLens asks GitHub whether the target repository is public; if so, the decision endpoint refuses (`409`) unless the request carries `confirm_public: true`, and the UI sends that only after the person ticks a specific warning. On a public repository the GitHub Action's comment is limited by the server (when the Action sends `public: true`, which it does unless the event says the repository is private) to the score, risk-signal names and file paths from the pull request itself: no ticket text, no names of people, no documents or dependents. A client that omits the flag gets the fuller comment, so use a token only for the repository it is meant for.
- An automated audit (`tests/test_security_edge.py`) fails the build if any endpoint other than the deliberate public ones works
  without a session, if a state-changing endpoint lacks a policy check, or if a viewer can change anything.

Known gaps: multi-factor authentication is optional per person, not enforceable for a workspace; there is no e-mail delivery, so invitation and reset links are handed over by an admin; the rate limits are in-process per worker (put a gateway limit in front for several
replicas); the session token is kept in the browser's local storage, so it relies on the strict Content-Security-Policy and the
absence of raw HTML rendering to stay out of reach of injected scripts (an HttpOnly cookie would remove that dependence at the cost
of CSRF handling); tokens issued before server-side sessions existed cannot be revoked individually (they expire); remembered answers are stored in the database unencrypted (redacted and
access-scoped, but protect the database itself); registration is open until `REPOLENS_ALLOW_REGISTRATION=false`, so turn it off
once your workspace exists; a generated-code execution sandbox is not implemented (the fix-generation agents are out of scope).

## What the sign-in page claims, and what backs it

The sign-in page tells people why they can trust the product. Each statement there must stay true; change the page and this table together.

| Claim on the page | Where it is true |
|---|---|
| Your data stays on your server; no AI service is contacted unless you connect one | Bring-your-own-model design; the default `heuristic` model and local hash embedder call no service. Settings → AI & search → "What leaves this server" lists every destination (`services/outbound.py`, `tests/test_outbound.py`) |
| People decide, not the AI | Fixes are drafts until approved and become a *draft* pull request (`services/fixes.py`); reports and wiki edits go through proposals; RepoLens never merges |
| Roles, per-repository access, authenticator-app sign-in, single sign-on, audit log | `services/policy.py`, `services/access.py`, `services/account_mfa.py`, `api/sso.py`, `api/audit.py`; `tests/test_security_edge.py` fails the build if an endpoint skips authorisation |
| Secrets are masked before indexing or sending to a model | `security.redact_secrets` and `redact_stored`; `tests/test_injection.py` checks a planted key never reaches a prompt |
| Open source, Apache 2.0, no usage data sent to the authors | `LICENSE`; the only outbound destinations are ones an administrator configures (see the table in the README's security section and `services/outbound.py`) |
| Honest limits: no independent audit, no compliance certification | True today. Do not add certification names to the page until an audit exists |

## The public Security and About pages

`/security` and `/about` are in-app pages anyone can open without signing in. The Security page lists every way data can leave the server, who can do what, how secrets and the AI are kept in check, how to verify the claims, honest limits, and how to report a problem. Its statements must stay true: change `frontend/src/pages/Security.tsx` and the table above together, and never add a certification or audit name unless one exists (`frontend/src/test/public-pages.test.tsx` fails if one appears).
