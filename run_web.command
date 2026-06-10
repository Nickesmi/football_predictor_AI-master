#!/usr/bin/env bash
# Start Football Predictor AI in browser mode:
#   API:      http://127.0.0.1:8001
#   Frontend: http://localhost:5173

set -euo pipefail

cd "$(dirname "$0")"

API_PORT="${API_PORT:-8001}"
WEB_PORT="${WEB_PORT:-5173}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
WEB_URL="http://localhost:${WEB_PORT}"
PIDS=()

kill_tree() {
  local pid="$1"
  local child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill_tree "$pid"
  done
}
trap cleanup EXIT INT TERM

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
  else
    (echo >/dev/tcp/127.0.0.1/"${port}") >/dev/null 2>&1
  fi
}

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PYTHON="venv/bin/python"
else
  PYTHON="python3"
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm was not found. Please install Node.js first: https://nodejs.org"
  exit 1
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

if port_in_use "${API_PORT}"; then
  echo "Port ${API_PORT} is already in use. Close the existing API server or run with API_PORT=8011."
  exit 1
fi

if port_in_use "${WEB_PORT}"; then
  echo "Port ${WEB_PORT} is already in use. Close the existing frontend server or run with WEB_PORT=5174."
  exit 1
fi

echo "Starting Football Predictor AI..."
echo "API:      http://127.0.0.1:${API_PORT}"
echo "Frontend: ${WEB_URL}"
echo ""

"${PYTHON}" -m uvicorn api.main:app --host 127.0.0.1 --port "${API_PORT}" &
PIDS+=("$!")

(cd frontend && npm run dev -- --host 127.0.0.1 --port "${WEB_PORT}" --strictPort) &
PIDS+=("$!")

for _ in {1..60}; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "One of the app servers stopped before startup completed."
      wait
      exit 1
    fi
  done

  if curl -fsS "${WEB_URL}" >/dev/null 2>&1; then
    if [ "${OPEN_BROWSER}" = "1" ] && command -v open >/dev/null 2>&1; then
      open "${WEB_URL}"
    fi
    break
  fi
  sleep 1
done

if ! curl -fsS "${WEB_URL}" >/dev/null 2>&1; then
  echo "Frontend did not become ready at ${WEB_URL}."
  exit 1
fi

echo "Ready. Keep this window open while using the app."
wait
