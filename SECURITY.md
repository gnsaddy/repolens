# Security

Report vulnerabilities privately to the maintainers; do not open public issues for them.

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

Known gaps: no multi-factor authentication; the sign-in limiter is in-process per worker (put a gateway rate limit in front
for several replicas); tokens issued before server-side sessions existed cannot be revoked individually (they expire);
remembered answers are stored in the database unencrypted (they are redacted for secrets and scoped by access, but protect the
database itself); generated-code execution sandbox is not yet implemented (the fix-generation agents are out of MVP scope).
