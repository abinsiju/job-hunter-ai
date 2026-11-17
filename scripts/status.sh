#!/usr/bin/env bash
# What is running right now.
cd "$(dirname "$0")/.."
check() { if curl -fs -o /dev/null -m 3 "$2" 2>/dev/null; then printf '  %-12s up    %s\n' "$1" "$3"; else printf '  %-12s DOWN  %s\n' "$1" "$3"; fi; }
check "dashboard" http://127.0.0.1:3100/login "http://127.0.0.1:3100"
check "api" http://127.0.0.1:8000/health "http://127.0.0.1:8000/health"
check "ollama" http://127.0.0.1:11434/api/version "local AI"
docker compose ps --format '  {{.Name}}  {{.Status}}' 2>/dev/null | sed 's/^/ /'
echo "  listening on: $(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk '/:(3100|8000|11434|5433|6380) /{print $9}' | sort -u | tr '\n' ' ')"
