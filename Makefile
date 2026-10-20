.PHONY: dev-api dev-web test lint up down seed
dev-api:   ## API with auto-reload on SQLite
	cd backend && . .venv/bin/activate && uvicorn repolens.main:app --reload --port 8000
dev-web:
	cd frontend && npm run dev
test:
	cd backend && . .venv/bin/activate && pytest
	cd frontend && npm test
lint:
	cd backend && . .venv/bin/activate && ruff check . && ruff format --check . && mypy repolens
	cd frontend && npm run lint && npm run typecheck
up:
	docker compose up --build -d
down:
	docker compose down
seed:      ## needs the API on :8000
	cd backend && . .venv/bin/activate && python scripts/seed_demo.py
