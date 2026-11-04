#!/usr/bin/env bash
# RepoLens AI: run it from a clone, on SQLite, with no Docker.
#
#   ./run-local.sh            set up (first run only) and start the API and the web UI
#   ./run-local.sh stop       stop them (add --purge to delete the local data too)
#   ./run-local.sh test       backend and frontend tests
#   ./run-local.sh docker     the full Docker Compose stack, built from this clone
#
# Ports: API_PORT=8001 WEB_PORT=5174 ./run-local.sh   (defaults 8000 and 5173).
# Without Python and Node, use the published images instead: docker compose -f docker-compose.release.yml up -d
# A language model is optional: add one under Settings, or set REPOLENS_LLM_PROVIDER and REPOLENS_LLM_API_KEY in backend/.env.
# Anything already set in the environment (for example REPOLENS_DATABASE_URL, to use PostgreSQL) wins over the defaults here.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN_DIR="$ROOT/.run"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
file_hash() { if have md5sum; then md5sum "$1" | cut -d' ' -f1; else md5 -q "$1"; fi; }

check_prereqs() {
  have python3 || die "Python 3.11+ is required"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11+ is required (found $(python3 --version 2>&1))"
  have node && have npm || die "Node.js 20+ is required (https://nodejs.org)"
  have curl || die "curl is required"
}

# install into backend/.venv and frontend/node_modules, again only when the dependency files change
setup() {
  [ -x "$BACKEND/.venv/bin/python" ] || python3 -m venv "$BACKEND/.venv" || die "could not create a virtual environment (on Debian/Ubuntu: sudo apt install python3-venv)"
  local stamp="$BACKEND/.venv/.installed" want
  want="$(file_hash "$BACKEND/pyproject.toml")"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$want" ]; then
    say "Installing backend dependencies (a few minutes the first time)"
    "$BACKEND/.venv/bin/pip" install --quiet --upgrade pip
    (cd "$BACKEND" && .venv/bin/pip install --quiet --default-timeout=120 --retries=6 -e ".[dev]")
    echo "$want" >"$stamp"
  fi
  stamp="$FRONTEND/node_modules/.installed"
  want="$(file_hash "$FRONTEND/package-lock.json")"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$want" ]; then
    say "Installing frontend dependencies"
    (cd "$FRONTEND" && npm ci --no-audit --no-fund --loglevel=error)
    echo "$want" >"$stamp"
  fi
}

wait_http() { # url, seconds
  local i
  for ((i = 0; i < $2; i++)); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done
  return 1
}

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  [ "${#PIDS[@]}" -gt 0 ] || return 0
  say "Stopping"
  kill "${PIDS[@]}" 2>/dev/null || true
  wait "${PIDS[@]}" 2>/dev/null || true
  rm -f "$RUN_DIR"/*.pid
}

cmd_up() {
  check_prereqs
  for p in "$API_PORT" "$WEB_PORT"; do
    ! port_in_use "$p" || die "port $p is in use. Pick others: API_PORT=8001 WEB_PORT=5174 ./run-local.sh"
  done
  mkdir -p "$RUN_DIR" "$BACKEND/data/repos"
  setup

  export REPOLENS_ENV="${REPOLENS_ENV:-dev}"
  export REPOLENS_DATABASE_URL="${REPOLENS_DATABASE_URL:-sqlite:///$BACKEND/data/repolens.db}"
  export REPOLENS_REPOS_DIR="${REPOLENS_REPOS_DIR:-$BACKEND/data/repos}"
  export REPOLENS_LOG_FILE="${REPOLENS_LOG_FILE:-$RUN_DIR/repolens.log}"   # rotated; api.log keeps only start-up output and errors
  export REPOLENS_CORS_ORIGINS="${REPOLENS_CORS_ORIGINS:-[\"http://127.0.0.1:$WEB_PORT\",\"http://localhost:$WEB_PORT\"]}"

  trap cleanup EXIT INT TERM
  say "Starting the API on http://127.0.0.1:$API_PORT"
  (cd "$BACKEND" && exec .venv/bin/uvicorn repolens.main:app --no-access-log --host 127.0.0.1 --port "$API_PORT") >"$RUN_DIR/api.log" 2>&1 &
  PIDS+=("$!"); echo "$!" >"$RUN_DIR/api.pid"
  wait_http "http://127.0.0.1:$API_PORT/healthz" 60 || { tail -n 25 "$RUN_DIR/api.log" >&2; die "the API did not start (log: $RUN_DIR/api.log)"; }

  say "Starting the web UI on http://127.0.0.1:$WEB_PORT"
  (cd "$FRONTEND" && VITE_API_PROXY="http://127.0.0.1:$API_PORT" exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort) >"$RUN_DIR/web.log" 2>&1 &
  PIDS+=("$!"); echo "$!" >"$RUN_DIR/web.pid"
  wait_http "http://127.0.0.1:$WEB_PORT/" 60 || { tail -n 25 "$RUN_DIR/web.log" >&2; die "the web UI did not start (log: $RUN_DIR/web.log)"; }

  cat <<EOF

RepoLens AI is running

  Web UI    http://127.0.0.1:$WEB_PORT      (first time: "Create a workspace"; the first user is the admin)
  API docs  http://127.0.0.1:$API_PORT/docs
  Logs      $RUN_DIR/api.log  $RUN_DIR/web.log
  Ctrl-C stops it.

EOF
  while kill -0 "${PIDS[0]}" 2>/dev/null && kill -0 "${PIDS[1]}" 2>/dev/null; do sleep 1; done
  echo "a process exited; see $RUN_DIR/*.log" >&2
}

cmd_docker() {
  have docker && docker compose version >/dev/null 2>&1 || die "Docker with Compose v2 is required"
  cd "$ROOT"
  [ -f .env ] || { cp .env.example .env; echo "created .env from .env.example; set strong secrets before sharing this."; }
  docker compose up --build
}

cmd_test() {
  check_prereqs
  setup
  say "Backend tests"
  (cd "$BACKEND" && .venv/bin/ruff check . && .venv/bin/pytest -q)
  say "Frontend checks"
  (cd "$FRONTEND" && npm run --silent typecheck && npm run --silent lint && npm test --silent)
}

cmd_stop() {
  local any=0 f pid
  for f in "$RUN_DIR"/*.pid; do
    [ -e "$f" ] || continue
    pid="$(cat "$f")"
    if kill "$pid" 2>/dev/null; then say "stopped $(basename "$f" .pid) (pid $pid)"; any=1; fi
    rm -f "$f"
  done
  if [ "${1:-}" = "--purge" ]; then rm -rf "$BACKEND/data" "$RUN_DIR"; say "removed the local data"; fi
  [ "$any" = 1 ] || echo "nothing was running"
}

case "${1:-up}" in
  up | start | run) cmd_up ;;
  docker) cmd_docker ;;
  test) cmd_test ;;
  stop) shift; cmd_stop "${1:-}" ;;
  -h | --help | help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (try ./run-local.sh help)" ;;
esac
