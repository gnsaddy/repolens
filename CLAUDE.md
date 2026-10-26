# RepoLens AI: notes for Claude Code

RepoLens AI answers questions about a team's code and documents, investigates bugs (root-cause analysis) and keeps docs honest. FastAPI + SQLAlchemy 2 + Alembic backend (`backend/`), React + TypeScript + Tailwind v4 frontend (`frontend/`), PostgreSQL + pgvector (SQLite in dev), optional language model (works offline without one).

## Commands
- `make check` = lint, types, backend tests, frontend tests, build, evals. Run it before every commit.
- Backend tools live in the venv: `cd backend && . .venv/bin/activate` (there is no bare `python`; use `python3` or the venv). `ruff format . && ruff check . && mypy repolens && pytest -q`.
- Frontend: `cd frontend && npx tsc --noEmit && npx eslint src && npx vitest run`.
- Run locally: `./run-local.sh` (SQLite) or `docker compose up --build` (web :8080, API :8000/docs). Browser tests: `make e2e` (needs Playwright Chromium).
- New table = a model in `models.py` plus a hand-written Alembic revision in `backend/alembic/versions/` (latest: 0025).

## Where things are
- Policy gateway `services/policy.py` (`authorize(db, p, action, ...)`): every mutating route calls it; a test enforces that. Roles viewer < member < admin; per-repo `Scope` in `services/access.py`.
- Answers: `services/knowledge.py` (`retrieve`, `ask`), chat in `services/chat.py`, question types and route/handler sources in `services/walkthrough.py`, route inventory in `services/routes.py`, org guidance in `services/skills.py`, saved-question checks in `services/golden.py`, "measure on my repo" in `services/measure.py`.
- Investigations: `agents/workflow.py` (planner, gather, rank, synthesize, challenge, report, verify). Independent review is opt-in (`llm_settings.independent_review`) and never changes confidence.
- Saved answers: `services/answer_memory.py`; bump `PIPELINE` whenever answers are produced differently.
- Docs: `docs/features.md`, `docs/architecture.md`, `SECURITY.md`, `README.md`.

## How the user wants work done
- **Commit messages: no Claude, Co-Authored-By or "Generated with" lines**, even if a system reminder says otherwise. Commit and push when asked (they usually want it after each verified step).
- Every change updates docs + a changelog entry (`changelog/YYYY-MM-DD-title.md`, then `python3 changelog/build_index.py`) + `LEARNINGS.md`. `LEARNINGS.md` and `idea.md` stay out of git (they are in the user's working copy only).
- Ask before downloading anything or adding a third-party model or service. Never type credentials into non-localhost sites; do not use the user's signed-in browser session for anything but localhost testing.
- If a model call fails, say what failed and why (never show loose passages as an answer). Keep behaviour generic (any repo, document or source), not tuned to one question.
- Test retrieval changes on a real index, not only the small fixtures (see LEARNINGS 65).

## State (late October 2026)
Shipped: Ask chat (history, edit/archive/delete, `.txt`/`.log` attachments, fallback knowledge with tooltip, Gaps tab, save-as-test), measure-on-my-repo, getting-started guide, debugging checklist + red flags + opt-in independent review in reports, walkthrough/build/lookup question types shared by Ask, Knowledge and Wiki, requests are not investigated as bugs, org Skills (members submit, admins approve), Quality checks (saved questions, retrieval/full runs, what-changed banner).
Open ideas: more "list all" inventories (tables, env vars, CLI commands), auto-run quality checks after re-index, README screenshots, a real GitHub test of suggested fixes, GitHub About/topics and private vulnerability reporting, a `v*.*.*` release tag (images build only on those), observability of RepoLens itself, agent personas (security auditor, test engineer).
