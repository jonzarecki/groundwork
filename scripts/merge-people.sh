#!/usr/bin/env bash
set -euo pipefail

# merge-people.sh -- Execute the 6-step merge protocol deterministically.
# Usage: ./scripts/merge-people.sh --keep <id> --merge <id> --reason "explanation"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

KEEP_ID=""
MERGE_ID=""
REASON=""
RUN_ID=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --keep) KEEP_ID="$2"; shift 2 ;;
    --merge) MERGE_ID="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [ -z "$KEEP_ID" ] || [ -z "$MERGE_ID" ] || [ -z "$REASON" ]; then
  echo "Usage: $0 --keep <person_id> --merge <person_id> --reason \"explanation\" [--run-id <id>]"
  exit 1
fi

if [ ! -f "$DB_PATH" ]; then
  echo "No database at $DB_PATH"
  exit 1
fi

KEEP_NAME=$(sqlite3 "$DB_PATH" "SELECT name FROM people WHERE id = $KEEP_ID;")
MERGE_NAME=$(sqlite3 "$DB_PATH" "SELECT name FROM people WHERE id = $MERGE_ID;")

if [ -z "$KEEP_NAME" ]; then echo "Person $KEEP_ID not found"; exit 1; fi
if [ -z "$MERGE_NAME" ]; then echo "Person $MERGE_ID not found"; exit 1; fi

echo "Merging: $MERGE_NAME (#$MERGE_ID) -> $KEEP_NAME (#$KEEP_ID)"
echo "Reason: $REASON"

RUN_CLAUSE="NULL"
if [ -n "$RUN_ID" ]; then RUN_CLAUSE="$RUN_ID"; fi

sqlite3 "$DB_PATH" <<SQL
-- 1. Snapshot loser to merge_log
INSERT INTO merge_log (kept_person_id, merged_person_id, merged_person_snapshot, reason, run_id)
SELECT $KEEP_ID, $MERGE_ID,
  json_object(
    'id', id, 'name', name, 'email', email, 'company', company,
    'company_domain', company_domain, 'linkedin_url', linkedin_url,
    'interaction_score', interaction_score, 'sources', sources,
    'status', status, 'first_seen', first_seen, 'last_seen', last_seen
  ),
  '$(echo "$REASON" | sed "s/'/''/g")',
  $RUN_CLAUSE
FROM people WHERE id = $MERGE_ID;

-- 2. Reassign sightings
UPDATE sightings SET person_id = $KEEP_ID WHERE person_id = $MERGE_ID;

-- 3. Reassign matching_rules
UPDATE matching_rules SET person_id = $KEEP_ID WHERE person_id = $MERGE_ID;

-- 4. Update kept person with best fields
UPDATE people SET
  name = CASE
    WHEN (SELECT name FROM people WHERE id = $MERGE_ID) LIKE '% %' AND name NOT LIKE '% %'
    THEN (SELECT name FROM people WHERE id = $MERGE_ID)
    ELSE name END,
  company = COALESCE(company, (SELECT company FROM people WHERE id = $MERGE_ID)),
  company_domain = COALESCE(company_domain, (SELECT company_domain FROM people WHERE id = $MERGE_ID)),
  linkedin_url = COALESCE(linkedin_url, (SELECT linkedin_url FROM people WHERE id = $MERGE_ID)),
  linkedin_confidence = COALESCE(linkedin_confidence, (SELECT linkedin_confidence FROM people WHERE id = $MERGE_ID)),
  first_seen = MIN(first_seen, COALESCE((SELECT first_seen FROM people WHERE id = $MERGE_ID), first_seen)),
  last_seen = MAX(last_seen, COALESCE((SELECT last_seen FROM people WHERE id = $MERGE_ID), last_seen)),
  sources = (SELECT GROUP_CONCAT(DISTINCT source) FROM sightings WHERE person_id = $KEEP_ID),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = $KEEP_ID;

-- 5. Recalculate score
UPDATE people SET interaction_score = (
  SELECT COALESCE(SUM(CASE interaction_type
    WHEN 'meeting' THEN 3 WHEN 'email_sent' THEN 2 WHEN 'email_received' THEN 1
    WHEN 'slack_dm' THEN 2 WHEN 'slack_channel' THEN 1 ELSE 0
  END), 0) FROM sightings WHERE person_id = $KEEP_ID
) WHERE id = $KEEP_ID;

-- 6. Delete merged person
DELETE FROM people WHERE id = $MERGE_ID;
SQL

echo ""
echo "Merge complete"
echo "  Kept:   #$KEEP_ID ($KEEP_NAME)"
echo "  Merged: #$MERGE_ID ($MERGE_NAME) -> deleted"
echo "  Sightings: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings WHERE person_id = $KEEP_ID;")"
echo "  Rules:     $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM matching_rules WHERE person_id = $KEEP_ID;")"
