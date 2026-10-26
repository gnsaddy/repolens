# Contributing

Thanks for helping. The short version: small, tested changes with the docs updated.

## Set up
See [docs/quickstart.md](docs/quickstart.md). `make help` lists every target.

## Before you open a pull request
```bash
make check        # what CI runs: lint (ruff, mypy, eslint), types, all tests, the production build and the evaluation gate. Run it before every push.
make eval         # if you touched retrieval, memory or routing: scores must not drop below evals/baselines
```
Backend: `ruff check . && ruff format .` (run on the whole folder), `mypy repolens`, `pytest`. Frontend: `npm test`, `npx tsc --noEmit`.

## What a good change includes
* Tests that fail without the change (`backend/tests`, `frontend/src/test`).
* Docs: behaviour goes in `docs/features.md`, design in `docs/architecture.md`, anything affecting trust in `SECURITY.md`.
* A changelog entry: `make changelog title="short title"` makes a draft in `changelog/unreleased/`; fill it in, then `make changelog-index` folds it into the version file (`changelog/v0.1-beta.md`).
* A database change gets an Alembic migration.

## Where things live
| You want to change | Look in |
|---|---|
| An API endpoint | `backend/repolens/api/` (thin) and `backend/repolens/services/` (the logic) |
| Who may do what | `services/policy.py` (every state-changing endpoint calls `authorize`) |
| Indexing, search, ranking | `services/indexer.py`, `retrieval.py`, `graph.py`, `rerank.py` |
| Investigations and root cause | `agents/workflow.py`, `services/cause_hints.py`, `services/investigations.py` |
| Models and routing | `services/llm.py`, `services/llm_router.py` |
| Suggested fixes | `services/fixes.py`, `api/fixes.py` |
| Editor (MCP) tools | `services/mcp_server.py` |
| Pull request Action | `integrations/github-action/` |
| Database tables | `backend/repolens/models.py` plus an Alembic migration in `backend/alembic/versions/` |
| Screens | `frontend/src/pages/`, shared pieces in `frontend/src/components/` |
| Quality scores | `backend/repolens/evals/`, baselines in `evals/baselines/` |

Design notes are in [docs/architecture.md](docs/architecture.md); what each feature does is in [docs/features.md](docs/features.md).

## Your first contribution
1. Look for issues labelled `good first issue` or `help wanted`, or open one to say what you plan to do (a short note avoids duplicate work).
2. Fork the repository and create a branch: `git checkout -b fix/short-description`.
3. Make the change with a test, run `make check`, and update the docs and changelog.
4. Open a pull request using the template. Say what changed and why, and how you tested it. Small pull requests are reviewed faster.
5. A maintainer reviews it. Expect questions; they are about the change, not you. Please keep the discussion respectful (see the [Code of Conduct](CODE_OF_CONDUCT.md)).

Easy places to start: improve a message or doc, add a test for an uncovered case, add a language to the code parser, or add a case to the evals.

## Asking for help
Open a GitHub issue with the question. Inside the app, **Report a bug / request a feature** (bottom of the sidebar) opens the same issue forms.

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
