#!/usr/bin/env bash
set -euo pipefail

source .env 2>/dev/null || true

echo "LC_SELF_EMAIL=${LC_SELF_EMAIL:-<not set>}"
echo "LC_COLLECT_DAYS=${LC_COLLECT_DAYS:-7}"
echo "LC_ENRICH_BATCH_SIZE=${LC_ENRICH_BATCH_SIZE:-10}"
echo "LC_MAX_PARTICIPANTS=${LC_MAX_PARTICIPANTS:-80}"

if [ -f data/contacts.db ]; then
    echo "DB exists"
    sqlite3 data/contacts.db "SELECT 'people: ' || COUNT(*) FROM people; SELECT 'sightings: ' || COUNT(*) FROM sightings; SELECT 'runs: ' || COUNT(*) FROM runs;"
else
    echo "DB missing -- run: sqlite3 data/contacts.db < schema.sql"
    exit 1
fi
