# Try it in five minutes

With only Docker, the shortest way to see the app is the README's Quick start (`docker compose -f docker-compose.release.yml up -d`, then http://localhost:8080). The steps below run it from a clone, which also lets you add the sample repository: you need Python 3.11+ and Node 20+ (`./run-local.sh` can start the API and web for you in place of the two `make dev-*` commands). No AI model, account or key is required: search, git-history correlation and ranking run on your machine without calling any AI service.

```bash
git clone https://github.com/gnsaddy/repolens.git && cd repolens
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && cd ..
(cd frontend && npm install)
make sample        # creates ./sample-repo, a tiny service with a known regression
make dev-api       # terminal 1: API on :8000 (SQLite)
make dev-web       # terminal 2: web on :5173
```

1. Open http://localhost:5173 and create a workspace (the first user is the admin).
2. **Repositories → Add**: source *Local path*, path = the `sample-repo` folder, then **Index**.
3. **Investigations → New**: paste the bug report that `make sample` printed. Within seconds you get a ranked cause that cites the exact commit and lines, with a confidence score and a timeline of what was checked.
4. Press **Confirm** on the root cause. Now try **Draft a runbook**, **Propose a fix** (needs a connected model for anything beyond simple cases), **Change Risk** on the same commit, and **Ask** on the Knowledge page.

Everything runs on your machine. To use a model for better wording, add one under **Settings**; RepoLens never sends anything to a model you did not connect. For Docker, Kubernetes and production settings see the [README](../README.md) and [operations guide](operations.md).
