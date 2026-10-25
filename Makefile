.PHONY: help eval eval-baseline run stop dev-api dev-web test test-api test-web test-pg lint fmt typecheck build migrate up down logs clean check
.DEFAULT_GOAL := help

VENV = cd backend && . .venv/bin/activate

help:      ## list the targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

run:       ## whole stack on PostgreSQL (API :8000, web :5173, reachable on your network)
	./run-local.sh up --lan --postgres
stop:      ## stop the local stack
	./run-local.sh stop
logs:      ## tail the API log
	tail -f .run/api.log
dev-api:   ## API with auto-reload on SQLite
	$(VENV) && uvicorn repolens.main:app --reload --port 8000
dev-web:   ## Vite dev server (proxies /api to :8000)
	cd frontend && npm run dev

test: test-api test-web   ## all tests
test-api:  ## backend tests on SQLite
	$(VENV) && pytest -q
test-web:  ## frontend tests
	cd frontend && npm test
test-pg:   ## backend tests on PostgreSQL + pgvector (set REPOLENS_TEST_DATABASE_URL)
	$(VENV) && REPOLENS_TEST_DATABASE_URL=$${REPOLENS_TEST_DATABASE_URL:?set REPOLENS_TEST_DATABASE_URL} pytest -q

eval:      ## quality gate: documents, answer memory, routing vs the recorded baseline
	$(VENV) && python -m repolens.evals.suites --baseline ../evals/baselines/suites.json --check
eval-baseline: ## record new baselines (only after an improvement you have reviewed)
	$(VENV) && python -m repolens.evals.suites --baseline ../evals/baselines/suites.json --update-baseline

lint:      ## ruff, mypy and eslint
	$(VENV) && ruff check . && ruff format --check . && mypy repolens
	cd frontend && npm run lint
fmt:       ## format and auto-fix the backend
	$(VENV) && ruff check --fix . && ruff format .
typecheck: ## TypeScript
	cd frontend && npm run typecheck
check: lint typecheck test   ## everything CI runs, locally
build:     ## production build of the web app
	cd frontend && npm run build
migrate:   ## apply database migrations (uses REPOLENS_DATABASE_URL)
	$(VENV) && alembic upgrade head

up:        ## Docker: build and start
	docker compose up --build -d
down:      ## Docker: stop
	docker compose down
clean:     ## remove caches and build output (keeps your data)
	rm -rf frontend/dist backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache
