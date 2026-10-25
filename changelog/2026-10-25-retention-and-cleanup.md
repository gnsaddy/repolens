# Retention sweep and workspace deletion

### Added
- A daily sweep removes records past their retention: remembered answers past their lifetime, feedback after 365 days, audit events after 730 days, ended or expired sign-ins and used or expired invitation links after 30 days, expired cache entries. Settings: `REPOLENS_RETENTION_FEEDBACK_DAYS`, `_AUDIT_DAYS`, `_SESSIONS_DAYS`, `_INVESTIGATION_DAYS` (0 keeps forever; investigations default to forever). Admins can run it now with `POST /api/settings/retention/run`; `GET /api/settings/retention` shows the policy.
- `python -m repolens.devtools.delete_workspace --email <person> [--yes]` deletes a whole workspace and everything in it (a dry run by default), for test workspaces and deletion requests.

### Fixed
- A development database made before the authenticator columns existed could not start: the helper that adds missing columns skipped NOT NULL JSON list columns. It now adds them with an empty-list default.
