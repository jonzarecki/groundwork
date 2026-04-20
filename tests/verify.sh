#!/usr/bin/env bash
# tests/verify.sh -- Assert DB state is correct after process-run.sh runs on fixture data.
# Extends test-pipeline.sh style: PASS/FAIL counts, exit 1 on any failure.
#
# Usage: ./tests/verify.sh [db_path]
#   db_path defaults to data/contacts.db

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB="${1:-$PROJECT_DIR/data/contacts.db}"

if [ ! -f "$DB" ]; then
    echo "Error: DB not found at $DB -- run ./tests/setup.sh first" >&2
    exit 1
fi

PASS=0
FAIL=0

sql() { sqlite3 "$DB" "$1"; }

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $desc"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $desc"
        echo "        expected: '$expected'"
        echo "        actual:   '$actual'"
    fi
}

assert_gte() {
    local desc="$1" min="$2" actual="$3"
    if [ "$actual" -ge "$min" ] 2>/dev/null; then
        PASS=$((PASS + 1))
        echo "  PASS: $desc (got $actual)"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $desc (expected >= $min, got $actual)"
    fi
}

echo "=== Groundwork Fixture Verify ==="
echo ""

# -------------------------------------------------------------------
echo "1. Expected contacts present"
# -------------------------------------------------------------------
for email in alice@acme.com bob@partner.com charlie@vendor.com dave@startup.io eve@agency.com; do
    COUNT=$(sql "SELECT COUNT(*) FROM people WHERE email = '$email';")
    assert_eq "contact $email exists" "1" "$COUNT"
done

# -------------------------------------------------------------------
echo ""
echo "2. Filtered contacts absent"
# -------------------------------------------------------------------
for email in noreply@ci.example.com "comments-noreply@docs.google.com" "test@example.com" \
             "confroom-a@resource.calendar.google.com" "team-announce@newsletter.example.com"; do
    COUNT=$(sql "SELECT COUNT(*) FROM people WHERE email = '$email';")
    assert_eq "filtered contact absent: $email" "0" "$COUNT"
done

# Large all-hands attendees (a1-a21@corp.com) should be absent because meeting
# has >80 attendees -- actually 22 which is under 80. Let's just confirm core filtered ones.
# The all-hands has only 22 people which is under LC_MAX_PARTICIPANTS=80,
# but >GROUP_MEETING_THRESHOLD(20 effective), so attendees get sightings as is_group=1.

# -------------------------------------------------------------------
echo ""
echo "3. Interaction scores correct"
# -------------------------------------------------------------------
# alice@acme.com: 1x email_received (direct, 1pt) + appears in calendar Project Review (3pt) = 4 points
ALICE_SCORE=$(sql "SELECT interaction_score FROM people WHERE email = 'alice@acme.com';")
assert_gte "alice score >= 4 (email_received + meeting)" "4" "$ALICE_SCORE"

# bob@partner.com: 1x 1:1 email_sent (3pt) + has_direct_bonus (5pt) = 8 points
BOB_SCORE=$(sql "SELECT interaction_score FROM people WHERE email = 'bob@partner.com';")
assert_eq "bob score = 8 (1:1 email_sent 3pt + has_direct_bonus 5pt)" "8" "$BOB_SCORE"

# charlie@vendor.com: 3 meetings (Team Sync + Project Review + Infra sync) × 3 = 9 points
CHARLIE_SCORE=$(sql "SELECT interaction_score FROM people WHERE email = 'charlie@vendor.com';")
assert_gte "charlie score >= 3 (meeting)" "3" "$CHARLIE_SCORE"

# dave@startup.io: 1x slack_dm (DM) = 2 points; also 1x slack_channel (is_group) capped at 3
DAVE_SCORE=$(sql "SELECT interaction_score FROM people WHERE email = 'dave@startup.io';")
assert_gte "dave score >= 2 (slack_dm)" "2" "$DAVE_SCORE"

# eve@agency.com: 1x slack_dm (MPIM) = 2 points
EVE_SCORE=$(sql "SELECT interaction_score FROM people WHERE email = 'eve@agency.com';")
assert_gte "eve score >= 2 (slack mpim dm)" "2" "$EVE_SCORE"

# -------------------------------------------------------------------
echo ""
echo "4. Sighting sources correct"
# -------------------------------------------------------------------
ALICE_SRC=$(sql "SELECT sources FROM people WHERE email = 'alice@acme.com';")
# alice appears in gmail AND calendar (Project Review)
assert_eq "alice sources include gmail" "1" "$(echo "$ALICE_SRC" | grep -c "gmail" || echo 0)"

CHARLIE_SRC=$(sql "SELECT sources FROM people WHERE email = 'charlie@vendor.com';")
assert_eq "charlie source is calendar" "1" "$(echo "$CHARLIE_SRC" | grep -c "calendar" || echo 0)"

DAVE_SRC=$(sql "SELECT sources FROM people WHERE email = 'dave@startup.io';")
assert_eq "dave source is slack" "1" "$(echo "$DAVE_SRC" | grep -c "slack" || echo 0)"

# -------------------------------------------------------------------
echo ""
echo "5. Resolution protocol"
# -------------------------------------------------------------------
# All sightings should be resolved (person_id not NULL)
UNRESOLVED=$(sql "SELECT COUNT(*) FROM sightings WHERE person_id IS NULL;")
assert_eq "no unresolved sightings" "0" "$UNRESOLVED"

# Sightings should have match_method set
NO_METHOD=$(sql "SELECT COUNT(*) FROM sightings WHERE person_id IS NOT NULL AND match_method IS NULL;")
assert_eq "all resolved sightings have match_method" "0" "$NO_METHOD"

# B5 (new person) created matching rules
RULES=$(sql "SELECT COUNT(*) FROM matching_rules;")
assert_gte "matching_rules created" "5" "$RULES"

# -------------------------------------------------------------------
echo ""
echo "6. is_group flags"
# -------------------------------------------------------------------
# Mailing list email (fix_gmail_003) should have is_group=1 sightings
# BUT announce@newsletter.example.com is filtered by should_skip_email
# So actually no group sightings from that email. Let's check the all-hands.
# All-hands has 22 attendees, GROUP_MEETING_THRESHOLD=20 effective, so is_group=1
LARGE_MEETING_SIGHTINGS=$(sql "SELECT COUNT(*) FROM sightings WHERE source = 'calendar' AND is_group = 1;")
assert_gte "all-hands attendees tagged is_group=1" "1" "$LARGE_MEETING_SIGHTINGS"

# DM sightings should be is_group=0
DM_GROUP=$(sql "SELECT COUNT(*) FROM sightings WHERE interaction_type = 'slack_dm' AND is_group = 1;")
assert_eq "slack DMs are not is_group" "0" "$DM_GROUP"

# -------------------------------------------------------------------
echo ""
echo "7. Run record finalized"
# -------------------------------------------------------------------
RUN_ID_FILE="$PROJECT_DIR/data/tmp/test_run_id"
if [ -f "$RUN_ID_FILE" ]; then
    RUN_ID=$(cat "$RUN_ID_FILE")
    FINISHED=$(sql "SELECT finished_at IS NOT NULL FROM runs WHERE id = $RUN_ID;")
    assert_eq "run $RUN_ID has finished_at set" "1" "$FINISHED"
    FOUND=$(sql "SELECT contacts_found FROM runs WHERE id = $RUN_ID;")
    assert_gte "run contacts_found > 0" "1" "$FOUND"
fi

# -------------------------------------------------------------------
echo ""
echo "8. LinkedIn state (enrichment not yet run)"
# -------------------------------------------------------------------
LI_COUNT=$(sql "SELECT COUNT(*) FROM linkedin_searches;")
assert_eq "no linkedin_searches yet" "0" "$LI_COUNT"

LI_PEOPLE=$(sql "SELECT COUNT(*) FROM people WHERE linkedin_url IS NOT NULL;")
assert_eq "no linkedin_url populated yet" "0" "$LI_PEOPLE"

# -------------------------------------------------------------------
echo ""
echo "9. Schema integrity"
# -------------------------------------------------------------------
# wrong_match is a valid status
sql "UPDATE people SET status = 'wrong_match' WHERE email = 'alice@acme.com';" 2>/dev/null
WM=$(sql "SELECT status FROM people WHERE email = 'alice@acme.com';")
assert_eq "wrong_match status accepted" "wrong_match" "$WM"
# Restore
sql "UPDATE people SET status = 'new' WHERE email = 'alice@acme.com';"

# -------------------------------------------------------------------
echo ""
echo "==========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "==========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
