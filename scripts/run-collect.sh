#!/usr/bin/env bash
set -euo pipefail

# run-collect.sh -- Single command for the entire collect pipeline.
# Chains: preflight -> run record -> collect-sources.py -> process-run.sh -> auto-merge -> report
#
# Usage: ./scripts/run-collect.sh [days]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

cd "$PROJECT_DIR"
source .env 2>/dev/null || true

DAYS="${1:-${LC_COLLECT_DAYS:-7}}"
EMAIL="${LC_SELF_EMAIL:-}"

if [ -z "$EMAIL" ]; then
  echo "Error: LC_SELF_EMAIL not set in .env" >&2
  exit 1
fi

# Find Python with MCP SDK
PYTHON="python3"
if ! $PYTHON -c "from mcp.client.sse import sse_client" 2>/dev/null; then
  for candidate in /Users/jzarecki/miniconda3/bin/python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null && $candidate -c "from mcp.client.sse import sse_client" 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  done
fi

echo "═══════════════════════════════════════"
echo " Linked Collector -- Collect (last ${DAYS}d)"
echo "═══════════════════════════════════════"
echo ""

# Preflight
"$SCRIPT_DIR/preflight.sh"
echo ""

# Create run record
RUN_ID=$(sqlite3 "$DB_PATH" "INSERT INTO runs (started_at, source) VALUES (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'all'); SELECT last_insert_rowid();")
PEOPLE_BEFORE_RUN=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
echo "Run #$RUN_ID started"
echo ""

# Phase 1: Collect from sources
echo "── Phase 1: Collect ──"
$PYTHON "$SCRIPT_DIR/collect-sources.py" --days "$DAYS" --email "$EMAIL" --output-dir "$PROJECT_DIR/data/tmp"
echo ""

# Phase 2: Process + Resolve
echo "── Phase 2: Process ──"
"$SCRIPT_DIR/process-run.sh" "$RUN_ID"
echo ""

# Auto-merge obvious duplicates
if [ -x "$SCRIPT_DIR/auto-merge.sh" ]; then
  echo "── Auto-merge ──"
  "$SCRIPT_DIR/auto-merge.sh" 2>/dev/null || true
  echo ""
fi

# Phase 4: Report
echo "── Report ──"
echo ""

TOTAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status != 'ignored';")
IGNORED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'ignored';")
PEOPLE_AFTER_RUN=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
NEW_THIS_RUN=$((PEOPLE_AFTER_RUN - PEOPLE_BEFORE_RUN))
WITH_LINKEDIN=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_url IS NOT NULL AND status != 'ignored';")
CONNECTED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'connected';")
SIGHTINGS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE run_id = $RUN_ID;")
UNRESOLVED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE person_id IS NULL;")
DUPES=$(sqlite3 "$DB_PATH" "
  SELECT COUNT(*) FROM (
    SELECT a.id FROM people a JOIN people b ON a.id < b.id
    WHERE LOWER(a.name) = LOWER(b.name) AND a.company_domain = b.company_domain
      AND a.status != 'ignored' AND b.status != 'ignored'
  );" 2>/dev/null || echo "0")
INCOMPLETE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE name NOT LIKE '% %' AND interaction_score >= 5 AND status != 'ignored';")
FLAGGED=$((UNRESOLVED + DUPES + INCOMPLETE))

echo "=== Collect Report (last ${DAYS} days) ==="
echo ""
echo "New contacts:    $NEW_THIS_RUN"
echo "Sightings:       $SIGHTINGS"
echo "Total:           $TOTAL ($IGNORED ignored, hidden)"
echo "With LinkedIn:   $WITH_LINKEDIN"
echo "Connected:       $CONNECTED"
echo ""

if [ "$NEW_THIS_RUN" -gt 0 ]; then
  echo "Top new contacts:"
  sqlite3 -separator '|' "$DB_PATH" "
    SELECT p.interaction_score, p.name, p.email, p.sources, p.status
    FROM people p
    JOIN sightings s ON s.person_id = p.id
    WHERE s.run_id = $RUN_ID AND p.status != 'ignored'
      AND p.id NOT IN (SELECT DISTINCT person_id FROM sightings WHERE run_id != $RUN_ID AND person_id IS NOT NULL)
    GROUP BY p.id
    ORDER BY p.interaction_score DESC
    LIMIT 10;
  " | while IFS='|' read -r score name email sources status; do
    printf "  [%3s] %-25s %-30s %s\n" "$score" "$name" "$email" "$sources"
  done
  echo ""
fi

echo "Flagged for review: $FLAGGED"
if [ "$UNRESOLVED" -gt 0 ]; then echo "  Unresolved sightings: $UNRESOLVED"; fi
if [ "$DUPES" -gt 0 ]; then echo "  Duplicate pairs: $DUPES"; fi
if [ "$INCOMPLETE" -gt 0 ]; then echo "  Incomplete names: $INCOMPLETE"; fi
echo ""
echo "Run #$RUN_ID complete."
