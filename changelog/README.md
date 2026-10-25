# Changelog

One file per change worth knowing about, named `YYYY-MM-DD-short-title.md`, newest first below. Write for the person who runs or
uses RepoLens, not for the person who wrote the code: what changed, why it matters, and anything they must do.

**Adding an entry:** `make changelog title="short title"` creates the file from the template; fill it in, then run
`make changelog-index`. Update `docs/features.md` / `docs/architecture.md` / `SECURITY.md` in the same change when behaviour,
design or protections move. Entries are added in the same commit as the change.

Sections to use (leave out the empty ones): **Added**, **Changed**, **Fixed**, **Security**, **Upgrade notes**.

<!-- entries:start -->
- 2026-10-25 · [Refusing weak document matches: measured, not shipped](2026-10-25-weak-match-refusal-evaluated.md)
- 2026-10-25 · [Security hardening, Insights, evaluation suites and feedback](2026-10-25-security-insights-evals.md)
- 2026-10-25 · [Retention sweep and workspace deletion](2026-10-25-retention-and-cleanup.md)
- 2026-10-25 · [People moved into Settings; log rotation and cleaner logs](2026-10-25-people-in-settings-and-log-rotation.md)
- 2026-10-25 · ["What leaves this server" panel](2026-10-25-outbound-panel.md)
- 2026-10-25 · [Night mode](2026-10-25-night-mode.md)
- 2026-10-25 · [Jira: new ticket list, analysis and investigation of several tickets, and "Start from Jira"](2026-10-25-jira-redesign-and-batch.md)
- 2026-10-25 · [A fuller dashboard](2026-10-25-dashboard.md)
- 2026-10-25 · [Browser end-to-end tests](2026-10-25-browser-tests.md)
- 2026-10-25 · [Nightly database backups and a restore guide](2026-10-25-backups.md)
- 2026-10-25 · [Audit log page](2026-10-25-audit-log.md)
- 2026-10-25 · [Invitations, password-reset links and authenticator-app sign-in](2026-10-25-account-safety.md)
- 2026-10-22 · [Integrations, multi-model routing, Jira analysis and wiki tools](2026-10-22-integrations-models-jira-wiki.md)
- 2026-10-20 · [Initial platform](2026-10-20-initial-platform.md)
<!-- entries:end -->
