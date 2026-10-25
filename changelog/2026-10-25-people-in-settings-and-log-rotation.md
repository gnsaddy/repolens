# People moved into Settings; log rotation and cleaner logs

### Changed
- **People** is now a tab of Settings (admins only). `/people` redirects there; the sidebar no longer has a separate entry.
- Access logs are written by the application, not the web server: they contain the path, status, time taken and caller address, and never the query string (a query can hold what someone searched for). Polled endpoints (health checks, drift count, server load) are logged only when they fail.
- Request URLs of outbound calls (the HTTP client's own INFO lines, which include query strings) are no longer logged.

### Added
- Log rotation: `REPOLENS_LOG_FILE` writes logs to a file that rotates at `REPOLENS_LOG_MAX_MB` (default 10) keeping `REPOLENS_LOG_BACKUPS` (default 5) older files, so at most 60 MB on disk; warnings and errors still reach standard output. The local runner uses it (`.run/repolens.log`). Docker Compose logs are capped at 5 × 10 MB per container. `REPOLENS_LOG_LEVEL` sets the level.

### Upgrade notes
- The container image now starts uvicorn with `--no-access-log`; request lines come from the `repolens.access` logger instead.
