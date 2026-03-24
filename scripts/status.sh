#!/usr/bin/env bash
set -euo pipefail

# status.sh -- Print database status report.
# Usage: ./scripts/status.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

if [ ! -f "$DB_PATH" ]; then
  echo "No database found. Run ./scripts/run-collect.sh first."
  exit 1
fi

TOTAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people;")
NEW=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'new';")
REVIEWED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'reviewed';")
CONNECTED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'connected';")
IGNORED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'ignored';")

WITH_LI=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_url IS NOT NULL;")
WITHOUT_LI=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_url IS NULL AND status != 'ignored';")
LI_HIGH=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_confidence = 'high';")
LI_MED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_confidence = 'medium';")
LI_LOW=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE linkedin_confidence = 'low';")

SIGHTINGS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sightings;")
RULES=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM matching_rules;")

NEW_THIS_WEEK=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days');")

echo "Linked Collector Status"
echo "═══════════════════════"
echo ""
echo "Contacts: $TOTAL total"
echo "  New:       $NEW"
echo "  Reviewed:  $REVIEWED"
echo "  Connected: $CONNECTED"
echo "  Ignored:   $IGNORED"
echo ""
echo "LinkedIn: $WITH_LI with profile, $WITHOUT_LI without"
echo "  High:    $LI_HIGH"
echo "  Medium:  $LI_MED"
echo "  Low:     $LI_LOW"
echo ""
echo "Data: $SIGHTINGS sightings, $RULES matching rules"
echo "New this week: $NEW_THIS_WEEK"
echo ""

echo "Top unlinked contacts:"
echo "  Score | Name                      | Company        | Sources   | Last seen"
echo "  ──────┼───────────────────────────┼────────────────┼───────────┼──────────"
sqlite3 -separator '|' "$DB_PATH" "
  SELECT interaction_score, name,
    COALESCE(company, company_domain, ''),
    sources,
    SUBSTR(last_seen, 1, 10)
  FROM people
  WHERE linkedin_url IS NULL AND status != 'ignored' AND name LIKE '% %'
  ORDER BY interaction_score DESC
  LIMIT 10;
" | while IFS='|' read -r score name company sources last_seen; do
  printf "  %5s | %-25s | %-14s | %-9s | %s\n" "$score" "$name" "$company" "$sources" "$last_seen"
done
echo ""

echo "Recent runs:"
sqlite3 -separator '|' "$DB_PATH" "
  SELECT id, SUBSTR(started_at, 1, 10), source,
    COALESCE(contacts_found, 0), COALESCE(contacts_new, 0), COALESCE(contacts_updated, 0)
  FROM runs ORDER BY started_at DESC LIMIT 5;
" | while IFS='|' read -r id date source found new_c updated; do
  printf "  #%-3s %s  %-7s found:%-4s new:%-4s updated:%s\n" "$id" "$date" "$source" "$found" "$new_c" "$updated"
done
