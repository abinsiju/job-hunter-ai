#!/usr/bin/env bash
# Stop the dashboard, API, local AI, and the database containers (data volumes are kept).
cd "$(dirname "$0")/.."
for port in 3100 8000; do
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  [ -n "$pids" ] && kill $pids && echo "stopped port $port"
done
pkill -x ollama && echo "stopped ollama" || true
docker compose stop
echo "Stopped. Your data is kept."
