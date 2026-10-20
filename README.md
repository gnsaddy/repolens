# RepoLens AI

Repository intelligence and evidence-backed root-cause analysis. See [`idea.md`](idea.md) for the product vision and
[`docs/architecture.md`](docs/architecture.md) for what is implemented.

**Stack:** FastAPI · LangGraph · PostgreSQL + pgvector · Tree-sitter · Vite + React + TypeScript + Tailwind ·
OpenTelemetry/Prometheus · Docker · Kubernetes · GitHub Actions.

**Screens:** Dashboard, **Knowledge** (Ask with citations, interactive graph, document upload), Repository Explorer,
Investigation View (root cause, confidence, timeline, code/commit/doc/test/PR evidence, report, human review), **Change
Risk** (score a commit, branch or PR with signals, blast radius and tests to run), Token Usage & Cost, Jira, Settings (LLM
provider + key, GitHub sign-in).

**Knowledge base:** ingests source code, Markdown/RST/text docs (git or upload), commits + diffs and GitHub pull
requests into one hybrid index and a typed knowledge graph (calls, imports, tests, doc links/mentions, semantic
similarity, PR↔commit↔file). See [`docs/architecture.md`](docs/architecture.md), the feature checklist in [`docs/features.md`](docs/features.md) and the
benchmark in [`evals/`](evals/README.md).

## DeepMind Wiki

Open **DeepMind Wiki** in the sidebar to add READMEs, text files, web URLs or a Confluence space (Sources tab), then browse, ask, explore concepts and check documentation health. See [docs/features.md](docs/features.md#deepmind-wiki). To try Confluence without an instance: `python -m repolens.devtools.fake_confluence --port 8099 --token demo-token --email demo@example.com` (loopback URLs are accepted only when `REPOLENS_ENV=dev`).

## Run it (Docker)

```bash
cp .env.example .env            # set secrets
docker compose up --build       # web http://localhost:8080 , API http://localhost:8000/docs
```

Create a workspace on the login page (first user is admin). Add a language-model key under **Settings** (or via `REPOLENS_LLM_*`), upload docs under **Knowledge → Documents**, and **Connect with GitHub** under Settings (device-flow sign-in; register an OAuth App with Device Flow enabled and paste its Client ID) to sync pull requests or use **GitHub Models** as the LLM. Anthropic uses an API key; Claude-subscription and Copilot sign-in are not offered by those vendors for third-party apps. Mount git repos under `./data/local-repos` (mounted at
`/mnt/repos`) or add an https/ssh Git URL. Out of the box `LLM_PROVIDER=heuristic` and `EMBEDDING_PROVIDER=hash` run
fully offline: retrieval, git-history correlation and ranking work without any model; set `anthropic`/`openai` for LLM
narrative synthesis (and `REPOLENS_PRICING_JSON` for cost estimates).

Demo data: `cd backend && python scripts/seed_demo.py` builds a small repo containing a real regression and runs an
investigation on it.

## Develop

```bash
cd backend  && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest
make dev-api      # SQLite, auto-reload       |  make dev-web   # Vite on :5173, proxies /api
```

Tests: `pytest` (SQLite) and `REPOLENS_TEST_DATABASE_URL=postgresql+psycopg://... pytest` (pgvector); `npm test` for the UI.

## Deploy

* Kubernetes: `deploy/k8s` (kustomize). Create the secret from `secret.example.yaml` first, set the image owner/host,
  then `kubectl apply -k deploy/k8s`. Uses default-deny NetworkPolicies, non-root pods, read-only root FS.
* CI (`.github/workflows/ci.yml`): lint, mypy, tests on SQLite and pgvector, migrations, frontend lint/type/test/build,
  image builds, Trivy scan. CD (`cd.yml`): on `v*.*.*` tags pushes images to GHCR and rolls out (approval via the
  `production` environment; needs the `KUBECONFIG_B64` secret).

## Configuration & security
See `.env.example` and [`SECURITY.md`](SECURITY.md). Set `REPOLENS_ENV=prod`, a strong `REPOLENS_JWT_SECRET` and
`REPOLENS_SECRET_KEY` for any real deployment.
