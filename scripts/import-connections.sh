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
    echo "  2. Select 'Connections' and click 'Request archive'"
    echo "  3. Wait for LinkedIn's email (5-15 min), download ZIP"
    echo "  4. Extract Connections.csv and run:"
    echo "     ./scripts/import-connections.sh path/to/Connections.csv"
    exit 1
fi

echo "Importing LinkedIn connections from $CSV_PATH"

IMPORTED=0
SKIPPED=0
HEADER_FOUND=0

while IFS= read -r line; do
    # LinkedIn CSVs have notes at the top before the actual header row
    if [ "$HEADER_FOUND" -eq 0 ]; then
        if echo "$line" | grep -qi "First Name"; then
            HEADER_FOUND=1
        fi
        continue
    fi

    # Parse CSV fields (handle quoted fields with commas)
    # Columns: First Name, Last Name, URL, Email Address, Company, Position, Connected On
    PARSED=$(python3 -c "
import csv, io, sys, json
reader = csv.reader(io.StringIO(sys.stdin.read()))
for row in reader:
    if len(row) >= 7:
        print(json.dumps(row[:7]))
    elif len(row) >= 3:
        row.extend([''] * (7 - len(row)))
        print(json.dumps(row[:7]))
" <<< "$line" 2>/dev/null) || continue

    if [ -z "$PARSED" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    FIRST_NAME=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[0])")
    LAST_NAME=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[1])")
    URL=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[2])")
    EMAIL=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[3])")
    COMPANY=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[4])")
    POSITION=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[5])")
    CONNECTED_ON=$(echo "$PARSED" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())[6])")

    if [ -z "$URL" ] && [ -z "$FIRST_NAME" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Upsert: insert or update on linkedin_url conflict
    sqlite3 "$DB_PATH" "
        INSERT INTO linkedin_connections (first_name, last_name, linkedin_url, email, company, position, connected_on)
        VALUES (
            '$(echo "$FIRST_NAME" | sed "s/'/''/g")',
            '$(echo "$LAST_NAME" | sed "s/'/''/g")',
            '$(echo "$URL" | sed "s/'/''/g")',
            '$(echo "$EMAIL" | sed "s/'/''/g")',
            '$(echo "$COMPANY" | sed "s/'/''/g")',
            '$(echo "$POSITION" | sed "s/'/''/g")',
            '$(echo "$CONNECTED_ON" | sed "s/'/''/g")'
        )
        ON CONFLICT(linkedin_url) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            email = excluded.email,
            company = excluded.company,
            position = excluded.position,
            connected_on = excluded.connected_on;
    " 2>/dev/null && IMPORTED=$((IMPORTED + 1)) || SKIPPED=$((SKIPPED + 1))

done < "$CSV_PATH"

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
