#!/bin/bash
# One polite round: employer feeds + web search of employer career pages + source check + scoring. Read-only: it never applies to anything.
# Run by hand, or let launchd run it (see scripts/com.jobhunter.hunt.plist.example). Log: logs/hunt.log
set -u
cd "$(dirname "$0")/../backend" || exit 1
mkdir -p ../logs
lock=../logs/hunt.lock
if [ -e "$lock" ] && kill -0 "$(cat "$lock" 2>/dev/null)" 2>/dev/null; then echo "a hunt is already running"; exit 0; fi
echo $$ > "$lock"; trap 'rm -f "$lock"' EXIT
# Catch-up logic: launchd calls this every hour (and at login/wake). It only does a round when the last good one is 12+ hours old,
# so a closed lid or a Mac that was off never skips a round for longer than it takes to wake up. `scripts/hunt.sh --now` forces a round.
stamp=../logs/hunt.last
if [ "${1:-}" != "--now" ] && [ -f "$stamp" ] && [ $(( $(date +%s) - $(cat "$stamp") )) -lt 43200 ]; then exit 0; fi
curl -s -m 3 http://127.0.0.1:11434/api/version >/dev/null || { echo "Ollama is not running; start it first (scripts/start.sh)"; exit 1; }
{ date; uv run python -m app.cli discover hunt 2>&1 | grep -v "work-rights disagreement"; echo; } >> ../logs/hunt.log
# a round counts only if it reached the "Read N job ads" summary line (Ollama, network and database all worked)
if tail -n 60 ../logs/hunt.log | grep -q "^Read [0-9]* job ads"; then date +%s > "$stamp"; fi
tail -n 40 ../logs/hunt.log
