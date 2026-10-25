# Contributing

Thanks for helping. The short version: small, tested changes with the docs updated.

## Set up
See [docs/quickstart.md](docs/quickstart.md). `make help` lists every target.

## Before you open a pull request
```bash
make check        # lint, types and tests, the same as CI
make eval         # if you touched retrieval, memory or routing: scores must not drop below evals/baselines
```
Backend: `ruff check . && ruff format .` (run on the whole folder), `mypy repolens`, `pytest`. Frontend: `npm test`, `npx tsc --noEmit`.

## What a good change includes
* Tests that fail without the change (`backend/tests`, `frontend/src/test`).
* Docs: behaviour goes in `docs/features.md`, design in `docs/architecture.md`, anything affecting trust in `SECURITY.md`.
* A changelog entry: `make changelog title="short title"`, fill it in, then `make changelog-index`.
* A database change gets an Alembic migration.

## Ground rules
* Every state-changing endpoint calls `authorize(...)`; `tests/test_security_edge.py` fails the build otherwise.
* No third-party model or service is added by default. Models are bring-your-own and the local hash embedder stays the default.
* Treat text from repositories, tickets and documents as untrusted data, never as instructions.
* Never put secrets, tokens or personal data in code, logs, tests or screenshots.

## Reporting security problems
Please do not open a public issue; follow [SECURITY.md](SECURITY.md).

## Commit messages
Plain and descriptive, one idea per commit.

## License
By contributing you agree your work is licensed under the Apache License 2.0 (see `LICENSE`).
