#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"
SCHEMA_PATH="$PROJECT_DIR/schema.sql"

mkdir -p "$PROJECT_DIR/data"

if [ -f "$DB_PATH" ]; then
    echo "Database already exists at $DB_PATH"
    echo "To reset, delete it first: rm $DB_PATH"
    exit 0
fi

sqlite3 "$DB_PATH" < "$SCHEMA_PATH"
echo "Database created at $DB_PATH"
