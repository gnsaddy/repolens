#!/usr/bin/env bash
# RepoLens AI — run the whole project on your machine.
#
#   ./run-local.sh                 set up (first run only) and start API + web on SQLite
#   ./run-local.sh up --postgres   use a throwaway PostgreSQL + pgvector container (needs Docker) instead of SQLite
#   ./run-local.sh docker          the full Docker Compose stack (needs Docker)
#   ./run-local.sh test            backend (pytest) + frontend (vitest) tests
#   ./run-local.sh stop [--purge]  stop background processes / containers (--purge also deletes local data)
#   ./run-local.sh help
#
# Options for `up`:  --api-port N (8000)  --web-port N (5173)  --reload  --postgres  --llm anthropic|openai
#                    --lan  serve the web UI on all interfaces so another machine on your network can open it
#                           (the API stays on 127.0.0.1 and is reachable only through the UI's proxy; dev mode, so
#                            use a trusted network)
# An LLM is optional: without one, retrieval, graph, ranking and reports all work offline.
# Put model keys in backend/.env (REPOLENS_LLM_PROVIDER, REPOLENS_LLM_API_KEY) or add them in the UI under Settings.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN_DIR="$ROOT/.run"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
PG_PORT="${PG_PORT:-55433}"
PG_NAME="repolens-local-pg"
PG_VOLUME="repolens-local-pgdata"
RELOAD=0
LAN=0
POSTGRES=0
PURGE=0

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
say()  { printf '%s==>%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '%s ok%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%swarn%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------------------------------------------- helpers
have() { command -v "$1" >/dev/null 2>&1; }

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

need_port_free() {
  if port_in_use "$1"; then die "port $1 is already in use. Pick another: ./run-local.sh up --api-port 8001 --web-port 5174"; fi
}

check_prereqs() {
  have git || die "git is required"
  have python3 || die "python3 (3.11+) is required"
  python3 - <<'PY' || die "Python 3.11 or newer is required (found: $(python3 --version 2>&1))"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
  have node || die "Node.js 20+ is required (https://nodejs.org)"
  have npm || die "npm is required"
  local major
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 18 ] || die "Node.js 18+ is required (found $(node --version))"
}

file_hash() { if have md5sum; then md5sum "$1" | cut -d' ' -f1; else md5 -q "$1"; fi; }

setup_backend() {
  say "Backend: Python environment"
  if [ ! -x "$BACKEND/.venv/bin/python" ]; then
    if python3 -m venv "$BACKEND/.venv" 2>/dev/null; then
      :
    else
      warn "python3 -m venv failed (on Debian/Ubuntu: sudo apt install python3-venv); trying virtualenv"
      rm -rf "$BACKEND/.venv"
      if ! python3 -m virtualenv "$BACKEND/.venv" >/dev/null 2>&1; then
        python3 -m pip install --user --quiet virtualenv 2>/dev/null \
          || python3 -m pip install --user --quiet --break-system-packages virtualenv \
          || die "could not create a virtual environment. Install python3-venv (sudo apt install python3-venv) and retry."
        python3 -m virtualenv "$BACKEND/.venv" >/dev/null
      fi
    fi
  fi
  local stamp="$BACKEND/.venv/.installed" want
  want="$(file_hash "$BACKEND/pyproject.toml")"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$want" ]; then
    say "Backend: installing dependencies (first run takes a few minutes)"
    "$BACKEND/.venv/bin/pip" install --quiet --upgrade pip
    (cd "$BACKEND" && .venv/bin/pip install --quiet --default-timeout=120 --retries=6 -e ".[dev]")
    echo "$want" >"$stamp"
  fi
  ok "backend ready"
}

setup_frontend() {
  say "Frontend: node modules"
  local stamp="$FRONTEND/node_modules/.installed" want
  want="$(file_hash "$FRONTEND/package-lock.json")"
  if [ ! -d "$FRONTEND/node_modules" ] || [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$want" ]; then
    (cd "$FRONTEND" && npm ci --no-audit --no-fund --loglevel=error)
    echo "$want" >"$stamp"
  fi
  ok "frontend ready"
}

start_postgres() {
  have docker || die "--postgres needs Docker. Run without it to use SQLite."
  docker info >/dev/null 2>&1 || die "Docker is installed but not running"
  say "PostgreSQL + pgvector on port $PG_PORT"
  if ! docker ps -a --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    port_in_use "$PG_PORT" && die "port $PG_PORT is in use (set PG_PORT=...)"
    docker run -d --name "$PG_NAME" -e POSTGRES_PASSWORD=repolens -e POSTGRES_USER=repolens -e POSTGRES_DB=repolens \
      -p "127.0.0.1:$PG_PORT:5432" -v "$PG_VOLUME:/var/lib/postgresql/data" pgvector/pgvector:pg16 \
      -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null  # dev database: speed over crash safety
  else
    docker start "$PG_NAME" >/dev/null
  fi
  # wait until it accepts TCP connections (the image restarts once during first-time initialisation)
  PG_PORT="$PG_PORT" "$BACKEND/.venv/bin/python" - <<'PY' || die "PostgreSQL did not become ready"
import os, sys, time
import psycopg
port = os.environ["PG_PORT"]
for _ in range(60):
    try:
        psycopg.connect(f"postgresql://repolens:repolens@127.0.0.1:{port}/repolens", connect_timeout=3).close()
        sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
PY
  export REPOLENS_DATABASE_URL="postgresql+psycopg://repolens:repolens@127.0.0.1:$PG_PORT/repolens"
  ok "postgres ready"
}

wait_http() { # url, seconds
  local url="$1" tries="$2" i
  for ((i = 0; i < tries; i++)); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  if [ "${#PIDS[@]}" -gt 0 ]; then
    echo
    say "Stopping"
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done
  fi
  rm -f "$RUN_DIR"/*.pid
}

# ---------------------------------------------------------------------------------------------------- commands
cmd_up() {
  check_prereqs
  have curl || die "curl is required"
  need_port_free "$API_PORT"
  need_port_free "$WEB_PORT"
  mkdir -p "$RUN_DIR" "$BACKEND/data/repos"
  setup_backend
  setup_frontend
  [ "$POSTGRES" = 1 ] && start_postgres

  # Local development defaults. Anything in backend/.env or the environment still wins.
  export REPOLENS_ENV="${REPOLENS_ENV:-dev}"
  export REPOLENS_DATABASE_URL="${REPOLENS_DATABASE_URL:-sqlite:///$BACKEND/data/repolens.db}"
  export REPOLENS_REPOS_DIR="${REPOLENS_REPOS_DIR:-$BACKEND/data/repos}"
  export REPOLENS_CORS_ORIGINS="${REPOLENS_CORS_ORIGINS:-[\"http://127.0.0.1:$WEB_PORT\",\"http://localhost:$WEB_PORT\"]}"
  [ -n "${LLM_PROVIDER:-}" ] && export REPOLENS_LLM_PROVIDER="$LLM_PROVIDER"

  trap cleanup EXIT INT TERM
  say "Starting API on http://127.0.0.1:$API_PORT"
  local reload_flag=()
  [ "$RELOAD" = 1 ] && reload_flag=(--reload)
  (cd "$BACKEND" && exec .venv/bin/uvicorn repolens.main:app --host 127.0.0.1 --port "$API_PORT" ${reload_flag[@]+"${reload_flag[@]}"}) \
    >"$RUN_DIR/api.log" 2>&1 &
  PIDS+=("$!")
  echo "${PIDS[-1]}" >"$RUN_DIR/api.pid"
  wait_http "http://127.0.0.1:$API_PORT/healthz" 60 || { tail -n 25 "$RUN_DIR/api.log" >&2; die "API did not start (log: $RUN_DIR/api.log)"; }
  ok "API is up"

  local WEB_HOST="127.0.0.1" LAN_IP=""
  if [ "$LAN" = 1 ]; then
    WEB_HOST="0.0.0.0"
    LAN_IP="$( (hostname -I 2>/dev/null || true) | awk '{print $1}')"
  fi
  say "Starting web UI on http://127.0.0.1:$WEB_PORT"
  (cd "$FRONTEND" && VITE_API_PROXY="http://127.0.0.1:$API_PORT" exec ./node_modules/.bin/vite --host "$WEB_HOST" --port "$WEB_PORT" --strictPort) \
    >"$RUN_DIR/web.log" 2>&1 &
  PIDS+=("$!")
  echo "${PIDS[-1]}" >"$RUN_DIR/web.pid"
  wait_http "http://127.0.0.1:$WEB_PORT/" 60 || { tail -n 25 "$RUN_DIR/web.log" >&2; die "web UI did not start (log: $RUN_DIR/web.log)"; }
  ok "web UI is up"

  cat <<EOF

${B}RepoLens AI is running${N}

  Web UI     http://127.0.0.1:$WEB_PORT
$( [ -n "$LAN_IP" ] && printf '  On your LAN  http://%s:%s\n' "$LAN_IP" "$WEB_PORT" )
  API docs   http://127.0.0.1:$API_PORT/docs
  Database   ${REPOLENS_DATABASE_URL%%\?*}
  Logs       $RUN_DIR/api.log   $RUN_DIR/web.log

  First time? Open the web UI and "Create a workspace" (the first user is the admin).
  Press Ctrl-C to stop.

EOF
  # stay in the foreground until either process exits or the user interrupts
  while kill -0 "${PIDS[0]}" 2>/dev/null && kill -0 "${PIDS[1]}" 2>/dev/null; do sleep 1; done
  warn "a process exited; see $RUN_DIR/*.log"
}

cmd_docker() {
  have docker || die "Docker is required for this mode"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
  cd "$ROOT"
  if [ ! -f .env ]; then
    cp .env.example .env
    warn "created .env from .env.example. Edit it and set strong secrets before exposing this anywhere."
  fi
  say "Docker Compose: building and starting (web http://localhost:${WEB_PORT_DOCKER:-8080}, API http://localhost:${API_PORT_DOCKER:-8000}/docs)"
  API_PORT="${API_PORT_DOCKER:-8000}" WEB_PORT="${WEB_PORT_DOCKER:-8080}" docker compose up --build
}

cmd_test() {
  check_prereqs
  setup_backend
  setup_frontend
  say "Backend tests"
  (cd "$BACKEND" && .venv/bin/ruff check . && .venv/bin/pytest -q)
  say "Frontend checks"
  (cd "$FRONTEND" && npm run --silent typecheck && npm run --silent lint && npm test --silent)
  ok "all tests passed"
}

cmd_stop() {
  local any=0
  for f in "$RUN_DIR"/*.pid; do
    [ -e "$f" ] || continue
    pid="$(cat "$f")"
    if kill "$pid" 2>/dev/null; then say "stopped $(basename "$f" .pid) (pid $pid)"; any=1; fi
    rm -f "$f"
  done
  if have docker && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_NAME"; then
    docker stop "$PG_NAME" >/dev/null && say "stopped $PG_NAME"; any=1
    if [ "$PURGE" = 1 ]; then docker rm "$PG_NAME" >/dev/null; docker volume rm "$PG_VOLUME" >/dev/null 2>&1 || true; say "removed the local Postgres container and data"; fi
  fi
  if [ "$PURGE" = 1 ]; then rm -rf "$BACKEND/data" "$RUN_DIR"; say "removed local data ($BACKEND/data)"; fi
  [ "$any" = 1 ] || echo "nothing was running"
}

# ---------------------------------------------------------------------------------------------------- main
CMD="${1:-up}"
[ "$#" -gt 0 ] && shift || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --api-port) API_PORT="${2:?--api-port needs a value}"; shift 2 ;;
    --web-port) WEB_PORT="${2:?--web-port needs a value}"; shift 2 ;;
    --reload) RELOAD=1; shift ;;
    --lan) LAN=1; shift ;;
    --postgres) POSTGRES=1; shift ;;
    --purge) PURGE=1; shift ;;
    --llm) LLM_PROVIDER="${2:?--llm needs anthropic|openai}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (try ./run-local.sh help)" ;;
  esac
done

case "$CMD" in
  up | start | run) cmd_up ;;
  docker) cmd_docker ;;
  test) cmd_test ;;
  stop) cmd_stop ;;
  help | -h | --help) usage ;;
  *) die "unknown command: $CMD (try ./run-local.sh help)" ;;
esac
