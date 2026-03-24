#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"
CSV_PATH="${1:-$PROJECT_DIR/data/Connections.csv}"

if [ ! -f "$DB_PATH" ]; then
    echo "No database found at $DB_PATH"
    echo "Run ./scripts/setup.sh first."
    exit 1
fi

if [ ! -f "$CSV_PATH" ]; then
    echo "No Connections.csv found at $CSV_PATH"
    echo ""
    echo "To export from LinkedIn:"
    echo "  1. Go to https://www.linkedin.com/mypreferences/d/download-my-data"
    echo "  2. Select 'Download larger data archive' (full archive — Connections"
    echo "     is no longer available as a separate download)"
    echo "  3. Click 'Request archive' and wait for LinkedIn's email (10-30 min)"
    echo "  4. Download the ZIP, extract Connections.csv from it, and run:"
    echo "     ./scripts/import-connections.sh path/to/Connections.csv"
    exit 1
fi

TOTAL_LINES=$(wc -l < "$CSV_PATH" | tr -d ' ')
echo "Importing LinkedIn connections from $CSV_PATH ($TOTAL_LINES lines)"

IMPORTED=0
SKIPPED=0
HEADER_FOUND=0
PROCESSED=0
START_TIME=$(date +%s)

while IFS= read -r line; do
    # LinkedIn CSVs have notes at the top before the actual header row
    if [ "$HEADER_FOUND" -eq 0 ]; then
        if echo "$line" | grep -qi "First Name"; then
            HEADER_FOUND=1
            DATA_LINES=$((TOTAL_LINES - PROCESSED - 1))
            echo "  Header found, ~$DATA_LINES connections to import"
        fi
        PROCESSED=$((PROCESSED + 1))
        continue
    fi

    PROCESSED=$((PROCESSED + 1))
    COUNT=$((IMPORTED + SKIPPED))

    # Progress every 100 rows
    if [ $((COUNT % 100)) -eq 0 ] && [ "$COUNT" -gt 0 ]; then
        ELAPSED=$(( $(date +%s) - START_TIME ))
        RATE=$(( COUNT * 100 / (ELAPSED > 0 ? ELAPSED : 1) ))
        REMAINING=$(( (DATA_LINES - COUNT) * 100 / (RATE > 0 ? RATE : 1) ))
        printf "  %d / %d  (%d imported, %d skipped)  ~%ds remaining\n" \
            "$COUNT" "$DATA_LINES" "$IMPORTED" "$SKIPPED" "$REMAINING"
    fi

    # Parse CSV and extract all fields in one Python call
    # Columns: First Name, Last Name, URL, Email Address, Company, Position, Connected On
    FIELDS=$(python3 -c "
import csv, io, sys
reader = csv.reader(io.StringIO(sys.stdin.read()))
for row in reader:
    if len(row) < 3:
        sys.exit(1)
    row.extend([''] * (7 - len(row)))
    # Tab-separated, single-quote escaped for SQL
    print('\t'.join(f.replace(\"'\", \"''\") for f in row[:7]))
" <<< "$line" 2>/dev/null) || { SKIPPED=$((SKIPPED + 1)); continue; }

    if [ -z "$FIELDS" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    FIRST_NAME=$(echo "$FIELDS" | cut -f1)
    LAST_NAME=$(echo "$FIELDS" | cut -f2)
    URL=$(echo "$FIELDS" | cut -f3)
    EMAIL=$(echo "$FIELDS" | cut -f4)
    COMPANY=$(echo "$FIELDS" | cut -f5)
    POSITION=$(echo "$FIELDS" | cut -f6)
    CONNECTED_ON=$(echo "$FIELDS" | cut -f7)

    if [ -z "$URL" ] && [ -z "$FIRST_NAME" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Upsert: insert or update on linkedin_url conflict
    sqlite3 "$DB_PATH" "
        INSERT INTO linkedin_connections (first_name, last_name, linkedin_url, email, company, position, connected_on)
        VALUES ('$FIRST_NAME', '$LAST_NAME', '$URL', '$EMAIL', '$COMPANY', '$POSITION', '$CONNECTED_ON')
        ON CONFLICT(linkedin_url) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            email = excluded.email,
            company = excluded.company,
            position = excluded.position,
            connected_on = excluded.connected_on;
    " 2>/dev/null && IMPORTED=$((IMPORTED + 1)) || SKIPPED=$((SKIPPED + 1))

done < "$CSV_PATH"

ELAPSED=$(( $(date +%s) - START_TIME ))
echo "  Done in ${ELAPSED}s"

TOTAL=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM linkedin_connections;")

# Cross-reference: match connections to existing people
MATCHED_EMAIL=$(sqlite3 "$DB_PATH" "
UPDATE people SET
  linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc WHERE lc.email = people.email AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high',
  status = 'connected',
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE linkedin_url IS NULL
  AND email IN (SELECT email FROM linkedin_connections WHERE linkedin_url IS NOT NULL AND linkedin_url != '');
SELECT changes();
")

MATCHED_NAME=$(sqlite3 "$DB_PATH" "
UPDATE people SET
  linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc WHERE LOWER(people.name) = LOWER(lc.first_name || ' ' || lc.last_name) AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high',
  status = 'connected',
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE linkedin_url IS NULL
  AND LOWER(name) IN (SELECT LOWER(first_name || ' ' || last_name) FROM linkedin_connections WHERE linkedin_url IS NOT NULL AND linkedin_url != '');
SELECT changes();
")

# Create matching rules for newly connected people
sqlite3 "$DB_PATH" "
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT p.id, 'email', p.email, 'linkedin', 'high', 'Confirmed via LinkedIn connections import'
FROM people p
WHERE p.status = 'connected' AND p.email IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM matching_rules mr WHERE mr.identifier_type = 'email' AND mr.identifier_value = p.email);
" 2>/dev/null

echo ""
echo "Import complete"
echo "───────────────"
echo "  Imported: $IMPORTED connections"
echo "  Skipped:  $SKIPPED rows"
echo "  Total:    $TOTAL connections in DB"
echo ""
echo "Cross-reference"
echo "───────────────"
echo "  Matched by email: $MATCHED_EMAIL"
echo "  Matched by name:  $MATCHED_NAME"
echo "  Total connected:  $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM people WHERE status = 'connected';")"
