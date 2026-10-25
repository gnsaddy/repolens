# Suggested fixes with human approval

### Added
- **Propose a fix** on a confirmed investigation: a drafted change (exact replacements, readable diff, tests to add, syntax check) written by the connected model, or by a plain template for a missing dictionary key. Download it as a patch, reject it, or approve it to open a draft pull request. Endpoints: `POST/GET /api/investigations/{id}/fix-proposals`, `GET /api/fix-proposals/{id}`, `GET .../patch`, `POST .../decision`.
- Model routing has a new task, "Suggested code fixes" (strong tier); it appears in the routing table and token usage.
- GitHub client write helpers (branch, file update, draft pull request) used only after approval.
- Migration 0020 (`fix_proposals`).

### Security
- Never run, never merged; limited to three files and a short change; build, deployment, dependency and secrets files are refused; the change is refused if the files moved on since it was drafted; policy actions `fix.propose` and `fix.decide` need the member role.
