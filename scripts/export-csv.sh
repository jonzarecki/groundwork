#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"
OUTPUT="${1:-$PROJECT_DIR/data/contacts.csv}"

if [ ! -f "$DB_PATH" ]; then
    echo "No database found at $DB_PATH"
    echo "Run ./scripts/init-db.sh and then claude /collect first."
    exit 1
fi

sqlite3 -header -csv "$DB_PATH" \
    "SELECT name, email, company, interaction_score, sources, last_seen, linkedin_url, linkedin_confidence, status FROM people ORDER BY interaction_score DESC;" \
    > "$OUTPUT"

COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
echo "Exported $COUNT contacts to $OUTPUT"
