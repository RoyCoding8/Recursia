#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
API_URL="${API_URL:-http://127.0.0.1:8000}"
APP_URL="${APP_URL:-http://127.0.0.1:3000}"
FORCE_STUB_MODE="${FORCE_STUB_MODE:-0}"
READY_TIMEOUT_SECONDS="${READY_TIMEOUT_SECONDS:-25}"

BACKEND_PID=""
FRONTEND_PID=""

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[ERROR] Missing required command: $1"
    exit 1
  fi
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi

  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${port} )" | tail -n +2 | grep -q .
    return $?
  fi

  return 1
}

open_browser() {
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
    return
  fi

  if command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi

  exit "$exit_code"
}

wait_for_backend_ready() {
  require_cmd curl

  echo
  echo "Waiting for backend readiness at ${API_URL}/ready (up to ${READY_TIMEOUT_SECONDS}s)..."
  local elapsed=0
  while (( elapsed < READY_TIMEOUT_SECONDS )); do
    if curl --silent --fail "${API_URL}/ready" >/dev/null 2>&1; then
      echo "[OK] Backend is ready."
      return 0
    fi
    printf "."
    sleep 1
    elapsed=$((elapsed + 1))
  done

  echo
  echo "[ERROR] Backend is not ready after ${READY_TIMEOUT_SECONDS} seconds."
  echo "Check the backend logs above for the exact startup error."
  return 1
}

setup_all() {
  require_cmd uv
  require_cmd npm

  echo "==> Syncing backend dependencies with uv"
  (
    cd "$BACKEND_DIR"
    uv sync
  )

  echo "==> Installing frontend dependencies"
  (
    cd "$FRONTEND_DIR"
    if [[ -f package-lock.json ]]; then
      npm ci
    else
      npm install
    fi
  )
}

start_backend() {
  require_cmd uv

  if port_in_use 8000; then
    echo "[ERROR] Port 8000 is already in use."
    exit 1
  fi

  echo "==> Starting backend at ${API_URL}"
  if [[ "$FORCE_STUB_MODE" == "1" ]]; then
    echo "==> Stub mode ON (LLM_PROVIDER=stub)"
    (
      cd "$BACKEND_DIR"
      LLM_PROVIDER=stub uv run uvicorn main:app --host 127.0.0.1 --port 8000
    ) &
  else
    (
      cd "$BACKEND_DIR"
      uv run uvicorn main:app --host 127.0.0.1 --port 8000
    ) &
  fi

  BACKEND_PID=$!
}

start_frontend() {
  require_cmd npm

  if port_in_use 3000; then
    echo "[ERROR] Port 3000 is already in use."
    exit 1
  fi

  echo "==> Starting frontend at ${APP_URL}"
  (
    cd "$FRONTEND_DIR"
    NEXT_PUBLIC_API_BASE_URL="$API_URL" npm run dev -- -H 127.0.0.1 -p 3000
  ) &

  FRONTEND_PID=$!
}

run_backend_only() {
  trap cleanup EXIT INT TERM
  start_backend
  wait "$BACKEND_PID"
}

run_frontend_only() {
  trap cleanup EXIT INT TERM
  start_frontend
  wait "$FRONTEND_PID"
}

run_full_app() {
  trap cleanup EXIT INT TERM
  start_backend
  wait_for_backend_ready
  start_frontend
  echo "==> Opening ${APP_URL}"
  open_browser
  echo "==> Press Ctrl+C to stop both services."
  wait
}

print_menu() {
  cat <<'EOF'
Recursia Launcher

1) First-time setup
2) Start full app (backend + frontend)
3) Start backend only
4) Start frontend only
5) Toggle deterministic local mode
6) Exit
EOF
}

interactive_menu() {
  while true; do
    echo
    echo "Project folder: $PROJECT_ROOT"
    if [[ "$FORCE_STUB_MODE" == "1" ]]; then
      echo "Mode: Deterministic local demo (LLM_PROVIDER=stub)"
    else
      echo "Mode: Standard (uses backend .env / environment)"
    fi
    echo
    print_menu
    echo
    read -r -p "Enter 1-6 and press Enter: " choice

    case "$choice" in
      1) setup_all ;;
      2) run_full_app; return ;;
      3) run_backend_only; return ;;
      4) run_frontend_only; return ;;
      5)
        if [[ "$FORCE_STUB_MODE" == "1" ]]; then
          FORCE_STUB_MODE=0
          echo "Stub mode is now OFF."
        else
          FORCE_STUB_MODE=1
          echo "Stub mode is now ON."
        fi
        ;;
      6) exit 0 ;;
      *) echo "Please enter only 1, 2, 3, 4, 5, or 6." ;;
    esac
  done
}

main() {
  case "${1:-menu}" in
    setup) setup_all ;;
    full) run_full_app ;;
    backend) run_backend_only ;;
    frontend) run_frontend_only ;;
    stub)
      FORCE_STUB_MODE=1
      run_full_app
      ;;
    menu) interactive_menu ;;
    *)
      echo "Usage: $0 [setup|full|backend|frontend|stub|menu]"
      exit 1
      ;;
  esac
}

main "$@"
