#!/usr/bin/env bash
set -euo pipefail

# save-linkedin.sh -- Save a single LinkedIn enrichment result to the DB.
#
# Usage:
#   ./scripts/save-linkedin.sh --id <person_id> --url <linkedin_url> \
#       --confidence <high|medium|low|null> \
#       [--run-id <run_id>] [--query <search_query>] \
#       [--candidates '<json_array>'] [--notes <notes>]
#
# Pass --url null (or omit --url) to log a failed search with no result.
#
# Examples:
#   ./scripts/save-linkedin.sh --id 1078 --url https://www.linkedin.com/in/michael-kotelnikov/ \
#       --confidence high --notes "Name + Red Hat match, 20+ mutuals"
#
#   ./scripts/save-linkedin.sh --id 973 --url null --confidence null \
#       --query "Kavitha Srinivasan Red Hat" --notes "No Red Hat match found"

DB_PATH="${LC_DB_PATH:-$(dirname "$0")/../data/contacts.db}"

PERSON_ID=""
URL=""
CONFIDENCE="null"
RUN_ID=""
QUERY=""
CANDIDATES="[]"
NOTES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --id)        PERSON_ID="$2"; shift 2 ;;
        --url)       URL="$2";       shift 2 ;;
        --confidence) CONFIDENCE="$2"; shift 2 ;;
        --run-id)    RUN_ID="$2";    shift 2 ;;
        --query)     QUERY="$2";     shift 2 ;;
        --candidates) CANDIDATES="$2"; shift 2 ;;
        --notes)     NOTES="$2";     shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$PERSON_ID" ]]; then
    echo "Error: --id is required" >&2
    exit 1
fi

# Resolve run_id: use provided, else latest run
if [[ -z "$RUN_ID" ]]; then
    RUN_ID=$(sqlite3 "$DB_PATH" "SELECT MAX(id) FROM runs;")
fi

# Derive query from person name if not provided
if [[ -z "$QUERY" ]]; then
    PERSON_NAME=$(sqlite3 "$DB_PATH" "SELECT name FROM people WHERE id = $PERSON_ID;")
    COMPANY=$(sqlite3 "$DB_PATH" "SELECT COALESCE(company,'') FROM people WHERE id = $PERSON_ID;")
    QUERY="$PERSON_NAME${COMPANY:+ $COMPANY}"
fi

sql_esc() { echo "${1//\'/\'\'}"; }

URL_SQL="NULL"
CONFIDENCE_SQL="NULL"
if [[ -n "$URL" && "$URL" != "null" ]]; then
    URL_SQL="'$(sql_esc "$URL")'"
    CONFIDENCE_SQL="'$(sql_esc "$CONFIDENCE")'"
fi

sqlite3 "$DB_PATH" <<SQL
UPDATE people SET
  linkedin_url = $URL_SQL,
  linkedin_confidence = $CONFIDENCE_SQL,
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE id = $PERSON_ID;

INSERT INTO linkedin_searches
  (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
VALUES (
  $PERSON_ID,
  $RUN_ID,
  '$(sql_esc "$QUERY")',
  '$(sql_esc "$CANDIDATES")',
  $URL_SQL,
  $CONFIDENCE_SQL,
  '$(sql_esc "$NOTES")'
);
SQL

PERSON_NAME=$(sqlite3 "$DB_PATH" "SELECT name FROM people WHERE id = $PERSON_ID;")
if [[ -n "$URL" && "$URL" != "null" ]]; then
    echo "Saved: [$PERSON_ID] $PERSON_NAME -> $URL ($CONFIDENCE)"
else
    echo "Logged: [$PERSON_ID] $PERSON_NAME -> no match"
fi
