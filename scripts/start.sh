#!/usr/bin/env bash
# Start everything: Postgres + Redis (Docker), local AI (Ollama), the API (8000) and the dashboard (3100).
# Everything listens on 127.0.0.1 only. (The server launches are redirected and exec'd so no wrapper keeps the caller's pipe open.) Usage: scripts/start.sh [--rebuild]
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
wait_for() { for _ in $(seq 1 90); do curl -fs -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; echo "  ! $2 did not start; see logs/"; return 1; }

echo "Database and queue..."
docker compose up -d

echo "Local AI..."
if ! pgrep -x ollama >/dev/null; then
  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_KEEP_ALIVE=5m \
    OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 nohup ollama serve > logs/ollama.log 2>&1 < /dev/null &
fi

echo "API..."
if port_free 8000; then (cd backend && exec nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8000) > logs/api.log 2>&1 < /dev/null & fi
wait_for http://127.0.0.1:8000/health "API"

echo "Dashboard..."
STALE=$(find frontend/app frontend/components frontend/lib frontend/public frontend/proxy.ts frontend/next.config.ts -newer frontend/.next/BUILD_ID -type f 2>/dev/null | head -1 || true)
if [ "${1:-}" = "--rebuild" ] || [ ! -f frontend/.next/BUILD_ID ] || [ -n "$STALE" ]; then
  echo "  building (about a minute)..."
  (cd frontend && npm run build > ../logs/frontend-build.log 2>&1) || { echo "  ! build failed; see logs/frontend-build.log"; exit 1; }
fi
if port_free 3100; then (cd frontend && exec nohup npm run start -- -H 127.0.0.1 -p 3100) > logs/frontend.log 2>&1 < /dev/null & fi
wait_for http://127.0.0.1:3100/login "dashboard"

echo
echo "Ready:  http://127.0.0.1:3100   (sign in as admin)"
echo "Stop:   scripts/stop.sh     Status: scripts/status.sh"
