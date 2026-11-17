#!/usr/bin/env bash
# Dump the Postgres DB to ./backups/ (gitignored). Restore: gunzip -c FILE | docker compose exec -T postgres psql -U jobhunter jobhunter
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "backups/jobhunter_$(date +%Y%m%d_%H%M%S).sql.gz"
echo "Backup written to backups/"
