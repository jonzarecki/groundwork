#!/usr/bin/env bash
set -euo pipefail

# process-run.sh -- Single orchestrator for the collect pipeline.
# Chains: parse sources -> resolve sightings -> update people -> finalize run.
# Outputs structured summary for the agent to format into a report.
#
# Usage: ./scripts/process-run.sh <run_id> [gmail_file] [calendar_file] [slack_file]
# Default: ./scripts/process-run.sh <run_id> data/tmp/lc_gmail.txt data/tmp/lc_calendar.txt data/tmp/lc_slack.txt

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <run_id> [gmail_file] [calendar_file] [slack_file]"
  exit 1
fi

RUN_ID=$1
GMAIL_FILE="${2:-$PROJECT_DIR/data/tmp/lc_gmail.txt}"
CALENDAR_FILE="${3:-$PROJECT_DIR/data/tmp/lc_calendar.txt}"
SLACK_FILE="${4:-$PROJECT_DIR/data/tmp/lc_slack.txt}"

cd "$PROJECT_DIR"
source .env 2>/dev/null || true

# Snapshot counts before processing
PEOPLE_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
CONNECTED_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'connected';")
LINKEDIN_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_url IS NOT NULL;")

# Save scores before for delta calculation (persistent table, not TEMP)
sqlite3 "$DB_PATH" "DROP TABLE IF EXISTS _score_snapshot; CREATE TABLE _score_snapshot AS SELECT id, interaction_score AS old_score FROM people;" 2>/dev/null || true

echo "=== Process Run $RUN_ID ==="

# Phase A: Parse sources
GMAIL_COUNT=0
CALENDAR_COUNT=0
SLACK_COUNT=0

if [ -n "$GMAIL_FILE" ] && [ -f "$GMAIL_FILE" ]; then
  GMAIL_SQL=$(python3 scripts/parse-source.py --source gmail --run-id "$RUN_ID" --db-path "$DB_PATH" < "$GMAIL_FILE" 2>"$PROJECT_DIR/data/tmp/lc_parse_gmail.log")
  if [ -n "$GMAIL_SQL" ]; then
    echo "$GMAIL_SQL" | sqlite3 "$DB_PATH"
  fi
  GMAIL_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'gmail';")
  GMAIL_PARSE_LOG=$(cat "$PROJECT_DIR/data/tmp/lc_parse_gmail.log" 2>/dev/null || echo "")
  echo "  Gmail: $GMAIL_COUNT sightings $GMAIL_PARSE_LOG"
fi

if [ -n "$CALENDAR_FILE" ] && [ -f "$CALENDAR_FILE" ]; then
  CAL_SQL=$(python3 scripts/parse-source.py --source calendar --run-id "$RUN_ID" --db-path "$DB_PATH" < "$CALENDAR_FILE" 2>"$PROJECT_DIR/data/tmp/lc_parse_cal.log")
  if [ -n "$CAL_SQL" ]; then
    echo "$CAL_SQL" | sqlite3 "$DB_PATH"
  fi
  CALENDAR_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'calendar';")
  CAL_PARSE_LOG=$(cat "$PROJECT_DIR/data/tmp/lc_parse_cal.log" 2>/dev/null || echo "")
  echo "  Calendar: $CALENDAR_COUNT sightings $CAL_PARSE_LOG"
fi

if [ -n "$SLACK_FILE" ] && [ -f "$SLACK_FILE" ]; then
  SLACK_SQL=$(python3 scripts/parse-source.py --source slack --run-id "$RUN_ID" --db-path "$DB_PATH" < "$SLACK_FILE" 2>"$PROJECT_DIR/data/tmp/lc_parse_slack.log")
  if [ -n "$SLACK_SQL" ]; then
    echo "$SLACK_SQL" | sqlite3 "$DB_PATH"
  fi
  SLACK_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID AND source = 'slack';")
  SLACK_PARSE_LOG=$(cat "$PROJECT_DIR/data/tmp/lc_parse_slack.log" 2>/dev/null || echo "")
  echo "  Slack: $SLACK_COUNT sightings $SLACK_PARSE_LOG"
fi

TOTAL_SIGHTINGS=$((GMAIL_COUNT + CALENDAR_COUNT + SLACK_COUNT))
echo "  Total: $TOTAL_SIGHTINGS sightings"
echo ""

# Phase B+C: Resolve
echo "Resolving..."
if ! sqlite3 "$DB_PATH" < scripts/resolve-sightings.sql; then
  echo "  Warning: resolve-sightings.sql had errors (some sightings may be unresolved)" >&2
fi
echo ""

# Phase D: Update people
echo "Updating people..."
HOME_DOMAIN=$(echo "${LC_SELF_EMAIL:-}" | cut -d@ -f2)
sqlite3 "$DB_PATH" \
  -cmd "CREATE TEMP TABLE IF NOT EXISTS _home(d TEXT); INSERT INTO _home SELECT '$HOME_DOMAIN' WHERE '$HOME_DOMAIN' != '' AND NOT EXISTS (SELECT 1 FROM _home);" \
  < scripts/update-people.sql
echo ""

# Cleanup: remove non-person entries that slipped through (calendar resources, bots)
CLEANED=$(sqlite3 "$DB_PATH" "
DELETE FROM people WHERE
  email LIKE '%@resource.calendar.google.com'
  OR email LIKE '%@group.calendar.google.com'
  OR email LIKE 'noreply@%'
  OR email LIKE 'no-reply@%';
SELECT changes();
")
if [ "$CLEANED" -gt 0 ]; then
  echo "Cleaned $CLEANED non-person entries"
fi

# Finalize
sqlite3 "$DB_PATH" < scripts/finalize-run.sql
echo ""

# Structured summary
PEOPLE_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
CONNECTED_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'connected';")
LINKEDIN_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_url IS NOT NULL;")
IGNORED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'ignored';")
RULES=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM matching_rules;")
NEW_PEOPLE=$((PEOPLE_AFTER - PEOPLE_BEFORE))
NEW_CONNECTED=$((CONNECTED_AFTER - CONNECTED_BEFORE))
NEW_LINKEDIN=$((LINKEDIN_AFTER - LINKEDIN_BEFORE))

echo "=== Summary ==="
echo "NEW_CONTACTS=$NEW_PEOPLE"
echo "TOTAL_CONTACTS=$PEOPLE_AFTER"
echo "TOTAL_IGNORED=$IGNORED"
echo "TOTAL_LINKEDIN=$LINKEDIN_AFTER"
echo "NEW_LINKEDIN=$NEW_LINKEDIN"
echo "TOTAL_CONNECTED=$CONNECTED_AFTER"
echo "NEW_CONNECTED=$NEW_CONNECTED"
echo "TOTAL_RULES=$RULES"
echo "SIGHTINGS_THIS_RUN=$TOTAL_SIGHTINGS"
echo "GMAIL_SIGHTINGS=$GMAIL_COUNT"
echo "CALENDAR_SIGHTINGS=$CALENDAR_COUNT"
echo "SLACK_SIGHTINGS=$SLACK_COUNT"

# Score movers (people whose score changed this run)
echo ""
echo "=== Score Movers ==="
sqlite3 "$DB_PATH" "
SELECT p.id, p.name, p.email, p.interaction_score,
  p.interaction_score - COALESCE(ss.old_score, 0) as delta
FROM people p
LEFT JOIN _score_snapshot ss ON ss.id = p.id
WHERE p.status != 'ignored'
  AND p.id IN (SELECT DISTINCT person_id FROM sightings WHERE run_id = $RUN_ID)
  AND p.interaction_score != COALESCE(ss.old_score, 0)
ORDER BY (p.interaction_score - COALESCE(ss.old_score, 0)) DESC, p.interaction_score DESC
LIMIT 15;
" 2>/dev/null || echo "(score delta calculation skipped)"

# Cleanup snapshot
sqlite3 "$DB_PATH" "DROP TABLE IF EXISTS _score_snapshot;" 2>/dev/null || true

# B4 candidates (unresolved)
UNRESOLVED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE person_id IS NULL;")
echo ""
echo "UNRESOLVED_SIGHTINGS=$UNRESOLVED"

# Duplicate check
DUPES=$(sqlite3 "$DB_PATH" "
SELECT COUNT(*) FROM (
  SELECT a.id FROM people a JOIN people b ON a.id < b.id
  WHERE LOWER(a.name) = LOWER(b.name) AND a.company_domain = b.company_domain
    AND a.status != 'ignored' AND b.status != 'ignored'
);" 2>/dev/null || echo "0")
echo "DUPLICATE_PAIRS=$DUPES"

# Incomplete names
INCOMPLETE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE name NOT LIKE '% %' AND interaction_score >= 5 AND status != 'ignored';")
echo "INCOMPLETE_NAMES=$INCOMPLETE"

FLAGGED=$((UNRESOLVED + DUPES + INCOMPLETE))
echo "FLAGGED_TOTAL=$FLAGGED"
