#!/usr/bin/env bash
set -euo pipefail

# process-run.sh -- Single orchestrator for the collect pipeline.
# Chains: parse sources -> resolve sightings -> update people -> finalize run.
#
# Usage: ./scripts/process-run.sh <run_id> [gmail_file] [calendar_file] [slack_file]
#
# The agent's job is to call MCP tools and save responses to temp files,
# then run this script once. Everything here is deterministic.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <run_id> [gmail_file] [calendar_file] [slack_file]"
  exit 1
fi

RUN_ID=$1
GMAIL_FILE="${2:-}"
CALENDAR_FILE="${3:-}"
SLACK_FILE="${4:-}"

cd "$PROJECT_DIR"
source .env 2>/dev/null || true

echo "=== Process Run $RUN_ID ==="
echo ""

TOTAL_SIGHTINGS=0

if [ -n "$GMAIL_FILE" ] && [ -f "$GMAIL_FILE" ]; then
  echo "Parsing Gmail..."
  GMAIL_SQL=$(python3 scripts/parse-source.py --source gmail --run-id "$RUN_ID" --db-path "$DB_PATH" < "$GMAIL_FILE" 2>/dev/null)
  if [ -n "$GMAIL_SQL" ]; then
    echo "$GMAIL_SQL" | sqlite3 "$DB_PATH"
  fi
  GMAIL_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'gmail';")
  echo "  Gmail sightings: $GMAIL_COUNT"
  TOTAL_SIGHTINGS=$((TOTAL_SIGHTINGS + GMAIL_COUNT))
else
  echo "Skipping Gmail (no file)"
fi

if [ -n "$CALENDAR_FILE" ] && [ -f "$CALENDAR_FILE" ]; then
  echo "Parsing Calendar..."
  CAL_SQL=$(python3 scripts/parse-source.py --source calendar --run-id "$RUN_ID" --db-path "$DB_PATH" < "$CALENDAR_FILE" 2>/dev/null)
  if [ -n "$CAL_SQL" ]; then
    echo "$CAL_SQL" | sqlite3 "$DB_PATH"
  fi
  CAL_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'calendar';")
  echo "  Calendar sightings: $CAL_COUNT"
  TOTAL_SIGHTINGS=$((TOTAL_SIGHTINGS + CAL_COUNT))
else
  echo "Skipping Calendar (no file)"
fi

if [ -n "$SLACK_FILE" ] && [ -f "$SLACK_FILE" ]; then
  echo "Parsing Slack..."
  SLACK_SQL=$(python3 scripts/parse-source.py --source slack --run-id "$RUN_ID" --db-path "$DB_PATH" < "$SLACK_FILE" 2>/dev/null)
  if [ -n "$SLACK_SQL" ]; then
    echo "$SLACK_SQL" | sqlite3 "$DB_PATH"
  fi
  SLACK_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'slack';")
  echo "  Slack sightings: $SLACK_COUNT"
  TOTAL_SIGHTINGS=$((TOTAL_SIGHTINGS + SLACK_COUNT))
else
  echo "Skipping Slack (no file)"
fi

echo ""
echo "Total sightings: $TOTAL_SIGHTINGS"
echo ""

echo "Resolving sightings..."
sqlite3 "$DB_PATH" < scripts/resolve-sightings.sql

echo ""
echo "Updating people..."
sqlite3 "$DB_PATH" < scripts/update-people.sql

echo ""
echo "Finalizing run..."
sqlite3 "$DB_PATH" < scripts/finalize-run.sql
