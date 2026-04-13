#!/usr/bin/env bash
set -euo pipefail

# Pipeline integration tests -- tests schema, scripts, and resolution protocol
# Usage: ./scripts/test-pipeline.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SCHEMA="$PROJECT_DIR/schema.sql"
TEST_DB=$(mktemp /tmp/groundwork-test-XXXX.db)
PASS=0
FAIL=0

cleanup() { rm -f "$TEST_DB"; }
trap cleanup EXIT

run_sql() { sqlite3 "$TEST_DB" "$1"; }

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS + 1))
    echo "  PASS: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $desc (expected '$expected', got '$actual')"
  fi
}

echo "=== Groundwork Pipeline Tests ==="
echo ""

# ---------------------------------------------------------------
echo "1. Schema validation"
# ---------------------------------------------------------------
sqlite3 "$TEST_DB" < "$SCHEMA"
TABLES=$(run_sql "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
assert_eq "8 tables created" "8" "$TABLES"

for tbl in people sightings matching_rules merge_log linkedin_searches runs linkedin_connections; do
  EXISTS=$(run_sql "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$tbl';")
  assert_eq "table $tbl exists" "1" "$EXISTS"
done

IDX_COUNT=$(run_sql "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%';")
assert_eq "at least 15 indexes" "1" "$([ "$IDX_COUNT" -ge 15 ] && echo 1 || echo 0)"

INVALID=$(run_sql "INSERT INTO runs (started_at, source) VALUES ('2026-01-01T00:00:00Z', 'all');" 2>/dev/null; run_sql "INSERT INTO sightings (run_id, source, source_uid, interaction_type, interaction_at) VALUES (1, 'invalid_source', 'x', 'meeting', '2026-01-01');" 2>&1 || true)
assert_eq "CHECK constraint rejects invalid source" "1" "$(echo "$INVALID" | grep -c 'CHECK')"

echo ""

# ---------------------------------------------------------------
echo "2. resolve-sightings.sql -- B1 email rule"
# ---------------------------------------------------------------
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Alice Smith', 'alice@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'gmail', 0);
INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence)
VALUES (1, 'email', 'alice@corp.com', 'gmail', 'high');
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at, context)
VALUES (1, 'gmail', 'alice@corp.com', 'A. Smith', 'alice@corp.com', 'email_received', '2026-01-02', 'Test email');
"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/resolve-sightings.sql" > /dev/null 2>&1

RESOLVED=$(run_sql "SELECT person_id FROM sightings WHERE id = 1;")
assert_eq "B1 resolves sighting to person 1" "1" "$RESOLVED"
METHOD=$(run_sql "SELECT match_method FROM sightings WHERE id = 1;")
assert_eq "B1 sets match_method to exact_email" "exact_email" "$METHOD"

echo ""

# ---------------------------------------------------------------
echo "3. resolve-sightings.sql -- B1b fallback + auto-create rule"
# ---------------------------------------------------------------
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Bob Jones', 'bob@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'calendar', 0);
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at)
VALUES (1, 'gmail', 'bob@corp.com', 'Bob J.', 'bob@corp.com', 'email_sent', '2026-01-03');
"

B1_HIT=$(run_sql "SELECT COUNT(*) FROM matching_rules WHERE identifier_type = 'email' AND identifier_value = 'bob@corp.com';")
assert_eq "Bob has no email rule before resolve" "0" "$B1_HIT"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/resolve-sightings.sql" > /dev/null 2>&1

RESOLVED2=$(run_sql "SELECT person_id FROM sightings WHERE raw_email = 'bob@corp.com' AND run_id = 1;")
assert_eq "B1b resolves Bob via people.email" "2" "$RESOLVED2"
RULE_CREATED=$(run_sql "SELECT COUNT(*) FROM matching_rules WHERE identifier_value = 'bob@corp.com';")
assert_eq "B1b auto-creates email rule for Bob" "1" "$RULE_CREATED"

echo ""

# ---------------------------------------------------------------
echo "4. resolve-sightings.sql -- B2 slack_uid rule"
# ---------------------------------------------------------------
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Charlie Slack', NULL, 'corp.com', '2026-01-01', '2026-01-01', 'slack', 0);
INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence)
VALUES (3, 'slack_uid', 'U12345ABCDE', 'slack', 'high');
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_username, interaction_type, interaction_at)
VALUES (1, 'slack', 'U12345ABCDE', 'charlie', 'charlie', 'slack_dm', '2026-01-04');
"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/resolve-sightings.sql" > /dev/null 2>&1

RESOLVED3=$(run_sql "SELECT person_id FROM sightings WHERE source_uid = 'U12345ABCDE' AND run_id = 1;")
assert_eq "B2 resolves Slack sighting via slack_uid rule" "3" "$RESOLVED3"

echo ""

# ---------------------------------------------------------------
echo "5. resolve-sightings.sql -- B5 new person + rules"
# ---------------------------------------------------------------
run_sql "
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, raw_username, interaction_type, interaction_at)
VALUES (1, 'slack', 'U99999NEWBY', 'New Person', 'newperson@ext.com', 'newperson', 'slack_dm', '2026-01-05');
"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/resolve-sightings.sql" > /dev/null 2>&1

NEW_PERSON=$(run_sql "SELECT COUNT(*) FROM people WHERE email = 'newperson@ext.com';")
assert_eq "B5 creates new person" "1" "$NEW_PERSON"
NEW_RESOLVED=$(run_sql "SELECT person_id FROM sightings WHERE raw_email = 'newperson@ext.com';")
assert_eq "B5 links sighting to new person" "$(run_sql "SELECT id FROM people WHERE email = 'newperson@ext.com';")" "$NEW_RESOLVED"
NEW_RULES=$(run_sql "SELECT COUNT(*) FROM matching_rules WHERE person_id = (SELECT id FROM people WHERE email = 'newperson@ext.com');"  )
assert_eq "B5 creates rules for new person" "1" "$([ "$NEW_RULES" -ge 1 ] && echo 1 || echo 0)"

echo ""

# ---------------------------------------------------------------
echo "6. update-people.sql -- Phase D"
# ---------------------------------------------------------------
sqlite3 "$TEST_DB" < "$SCRIPT_DIR/update-people.sql" > /dev/null 2>&1

SCORE=$(run_sql "SELECT interaction_score FROM people WHERE email = 'alice@corp.com';")
assert_eq "Phase D: Alice score = 1 (email_received)" "1" "$SCORE"
BOB_SCORE=$(run_sql "SELECT interaction_score FROM people WHERE email = 'bob@corp.com';")
assert_eq "Phase D: Bob score = 2 (email_sent)" "2" "$BOB_SCORE"

echo ""

# ---------------------------------------------------------------
echo "7. merge-people.sh"
# ---------------------------------------------------------------
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Dupe One', 'dupe@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'gmail', 1);
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Dupe Two', 'dupe2@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'calendar', 3);
INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence)
VALUES ((SELECT id FROM people WHERE email = 'dupe@corp.com'), 'email', 'dupe@corp.com', 'gmail', 'high');
INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence)
VALUES ((SELECT id FROM people WHERE email = 'dupe2@corp.com'), 'email', 'dupe2@corp.com', 'calendar', 'high');
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at, person_id, match_method, match_confidence)
VALUES (1, 'gmail', 'dupe@corp.com', 'Dupe One', 'dupe@corp.com', 'email_received', '2026-01-06',
  (SELECT id FROM people WHERE email = 'dupe@corp.com'), 'exact_email', 'high');
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at, person_id, match_method, match_confidence)
VALUES (1, 'calendar', 'dupe2@corp.com', 'Dupe Two', 'dupe2@corp.com', 'meeting', '2026-01-06',
  (SELECT id FROM people WHERE email = 'dupe2@corp.com'), 'exact_email', 'high');
"

KEEP_ID=$(run_sql "SELECT id FROM people WHERE email = 'dupe@corp.com';")
MERGE_ID=$(run_sql "SELECT id FROM people WHERE email = 'dupe2@corp.com';")

# Test the merge SQL logic directly (merge-people.sh uses hardcoded DB_PATH)
run_sql "
  INSERT INTO merge_log (kept_person_id, merged_person_id, merged_person_snapshot, reason)
  SELECT $KEEP_ID, $MERGE_ID,
    json_object('id', id, 'name', name, 'email', email, 'interaction_score', interaction_score),
    'test merge'
  FROM people WHERE id = $MERGE_ID;
  UPDATE sightings SET person_id = $KEEP_ID WHERE person_id = $MERGE_ID;
  UPDATE matching_rules SET person_id = $KEEP_ID WHERE person_id = $MERGE_ID;
  UPDATE people SET
    sources = (SELECT GROUP_CONCAT(DISTINCT source) FROM sightings WHERE person_id = $KEEP_ID),
    interaction_score = (SELECT COALESCE(SUM(CASE interaction_type
      WHEN 'meeting' THEN 3 WHEN 'email_sent' THEN 2 WHEN 'email_received' THEN 1
      WHEN 'slack_dm' THEN 2 WHEN 'slack_channel' THEN 1 ELSE 0 END), 0)
      FROM sightings WHERE person_id = $KEEP_ID)
  WHERE id = $KEEP_ID;
  DELETE FROM people WHERE id = $MERGE_ID;
"

MERGE_LOGGED=$(run_sql "SELECT COUNT(*) FROM merge_log WHERE kept_person_id = $KEEP_ID;")
assert_eq "merge_log has entry" "1" "$MERGE_LOGGED"
SIGHTINGS_REASSIGNED=$(run_sql "SELECT COUNT(*) FROM sightings WHERE person_id = $KEEP_ID;")
assert_eq "both sightings point to kept person" "2" "$SIGHTINGS_REASSIGNED"
RULES_INHERITED=$(run_sql "SELECT COUNT(*) FROM matching_rules WHERE person_id = $KEEP_ID;")
assert_eq "kept person has both email rules" "2" "$RULES_INHERITED"
PERSON_GONE=$(run_sql "SELECT COUNT(*) FROM people WHERE id = $MERGE_ID;")
assert_eq "merged person deleted" "0" "$PERSON_GONE"

echo ""

# ---------------------------------------------------------------
echo "8. Phase D name improvement"
# ---------------------------------------------------------------
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Jsmith', 'jsmith@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'calendar', 0);
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at, person_id, match_method, match_confidence)
VALUES (1, 'gmail', 'jsmith@corp.com', 'John Smith', 'jsmith@corp.com', 'email_received', '2026-01-07',
  (SELECT id FROM people WHERE email = 'jsmith@corp.com'), 'exact_email', 'high');
"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/update-people.sql" > /dev/null 2>&1

UPDATED_NAME=$(run_sql "SELECT name FROM people WHERE email = 'jsmith@corp.com';")
assert_eq "Phase D: auto-derived name updated to full name" "John Smith" "$UPDATED_NAME"

echo ""

# ---------------------------------------------------------------
echo "9. LinkedIn cross-reference"
# ---------------------------------------------------------------
run_sql "
INSERT INTO linkedin_connections (first_name, last_name, linkedin_url, email, company)
VALUES ('Alice', 'Smith', 'https://linkedin.com/in/alicesmith/', 'alice@corp.com', 'Corp Inc');
INSERT INTO linkedin_connections (first_name, last_name, linkedin_url, email, company)
VALUES ('John', 'Smith', 'https://linkedin.com/in/johnsmith/', '', 'Corp Inc');

UPDATE people SET linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc WHERE lc.email = people.email AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high', status = 'connected'
WHERE linkedin_url IS NULL AND email IN (SELECT email FROM linkedin_connections WHERE linkedin_url IS NOT NULL AND linkedin_url != '');

UPDATE people SET linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc WHERE LOWER(people.name) = LOWER(lc.first_name || ' ' || lc.last_name) AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high', status = 'connected'
WHERE linkedin_url IS NULL AND LOWER(name) IN (SELECT LOWER(first_name || ' ' || last_name) FROM linkedin_connections WHERE linkedin_url IS NOT NULL AND linkedin_url != '');
"
ALICE_LI=$(run_sql "SELECT linkedin_url FROM people WHERE email = 'alice@corp.com';")
assert_eq "cross-ref by email matches Alice" "https://linkedin.com/in/alicesmith/" "$ALICE_LI"
JOHN_LI=$(run_sql "SELECT linkedin_url FROM people WHERE name = 'John Smith';")
assert_eq "cross-ref by name matches John Smith" "https://linkedin.com/in/johnsmith/" "$JOHN_LI"

echo ""

# ---------------------------------------------------------------
echo "10. LinkedIn search logging"
# ---------------------------------------------------------------
run_sql "
INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
VALUES (1, 1, 'search_people: Alice Smith Corp', '[{\"url\":\"/in/alicesmith/\"}]', 'https://linkedin.com/in/alicesmith/', 'high', 'Match');
INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
VALUES (2, 1, 'search_people: Bob Jones Corp', NULL, NULL, NULL, 'No results');
"
SEARCHES=$(run_sql "SELECT COUNT(*) FROM linkedin_searches;")
assert_eq "2 linkedin_searches logged" "2" "$SEARCHES"
FAILED=$(run_sql "SELECT COUNT(*) FROM linkedin_searches WHERE chosen_url IS NULL;")
assert_eq "failed search logged with NULL chosen_url" "1" "$FAILED"

echo ""

# ---------------------------------------------------------------
echo "11. parse-source.py (Gmail)"
# ---------------------------------------------------------------
GMAIL_INPUT="Message ID: test123
Subject: Hello World
From: Test User <test@example.com>
Date: Fri, 6 Mar 2026 10:00:00 +0000
To: jzarecki@redhat.com

---

Message ID: test456
Subject: Invitation: Meeting @ Mon
From: Calendar <calendar-invite@google.com>
Date: Fri, 6 Mar 2026 11:00:00 +0000
To: jzarecki@redhat.com"

GMAIL_SQL=$(echo "$GMAIL_INPUT" | LC_SELF_EMAIL=jzarecki@redhat.com python3 "$SCRIPT_DIR/parse-source.py" --source gmail --run-id 99 2>/dev/null)
GMAIL_COUNT=$(echo "$GMAIL_SQL" | grep -c "INSERT INTO sightings" || echo 0)
assert_eq "parse-source.py: parses 1 Gmail sighting (skips calendar invite)" "1" "$GMAIL_COUNT"
GMAIL_HAS_TEST=$(echo "$GMAIL_SQL" | grep -c "test@example.com" || echo 0)
assert_eq "parse-source.py: contains test@example.com" "1" "$GMAIL_HAS_TEST"

echo ""

# ---------------------------------------------------------------
echo "12. finalize-run.sql"
# ---------------------------------------------------------------
run_sql "INSERT INTO runs (started_at, source) VALUES ('2026-01-10T00:00:00Z', 'all');"
LATEST_RUN=$(run_sql "SELECT MAX(id) FROM runs WHERE finished_at IS NULL;")
run_sql "INSERT INTO sightings (run_id, source, source_uid, raw_email, interaction_type, interaction_at, person_id, match_method, match_confidence)
VALUES ($LATEST_RUN, 'gmail', 'fin@test.com', 'fin@test.com', 'email_received', '2026-01-10', 1, 'exact_email', 'high');"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/finalize-run.sql" > /dev/null 2>&1

FINISHED=$(run_sql "SELECT finished_at IS NOT NULL FROM runs WHERE id = $LATEST_RUN;")
assert_eq "finalize-run.sql sets finished_at" "1" "$FINISHED"
FOUND=$(run_sql "SELECT contacts_found FROM runs WHERE id = $LATEST_RUN;")
assert_eq "finalize-run.sql counts sightings" "1" "$FOUND"

echo ""

# ---------------------------------------------------------------
echo "13. resolve-sightings.sql -- auto connection check"
# ---------------------------------------------------------------
# Alice already has linkedin_url from cross-ref test 9.
# Add a new person + connection entry to test auto-check via resolve.
run_sql "
INSERT INTO people (name, email, company_domain, first_seen, last_seen, sources, interaction_score)
VALUES ('Eve Connected', 'eve@corp.com', 'corp.com', '2026-01-01', '2026-01-01', 'gmail', 1);
INSERT INTO linkedin_connections (first_name, last_name, linkedin_url, email, company)
VALUES ('Eve', 'Connected', 'https://linkedin.com/in/eveconnected/', 'eve@corp.com', 'Corp');
INSERT INTO sightings (run_id, source, source_uid, raw_name, raw_email, interaction_type, interaction_at, person_id, match_method, match_confidence)
VALUES (1, 'gmail', 'eve@corp.com', 'Eve Connected', 'eve@corp.com', 'email_received', '2026-01-11',
  (SELECT id FROM people WHERE email = 'eve@corp.com'), 'exact_email', 'high');
"
# Eve has no linkedin_url yet, but linkedin_connections has her email.
EVE_STATUS_BEFORE=$(run_sql "SELECT status FROM people WHERE email = 'eve@corp.com';")
assert_eq "Eve starts as 'new'" "new" "$EVE_STATUS_BEFORE"

sqlite3 "$TEST_DB" < "$SCRIPT_DIR/resolve-sightings.sql" > /dev/null 2>&1

EVE_STATUS=$(run_sql "SELECT status FROM people WHERE email = 'eve@corp.com';")
assert_eq "resolve auto-sets Eve to 'connected'" "connected" "$EVE_STATUS"
EVE_URL=$(run_sql "SELECT linkedin_url FROM people WHERE email = 'eve@corp.com';")
assert_eq "resolve auto-sets Eve's linkedin_url from CSV" "https://linkedin.com/in/eveconnected/" "$EVE_URL"

echo ""

# ---------------------------------------------------------------
echo "14. slack_users cache table"
# ---------------------------------------------------------------
run_sql "
INSERT INTO slack_users (slack_uid, username, real_name, email, title)
VALUES ('UCACHE001', 'testuser', 'Test Cached User', 'cached@corp.com', 'Engineer');
"
CACHED=$(run_sql "SELECT real_name FROM slack_users WHERE slack_uid = 'UCACHE001';")
assert_eq "slack_users cache stores user" "Test Cached User" "$CACHED"
CACHE_COUNT=$(run_sql "SELECT COUNT(*) FROM slack_users;")
assert_eq "slack_users cache has entry" "1" "$([ "$CACHE_COUNT" -ge 1 ] && echo 1 || echo 0)"

echo ""

# ---------------------------------------------------------------
echo "15. Gmail skip patterns (new filters)"
# ---------------------------------------------------------------
GMAIL_FILTER_INPUT="Message ID: f1
Subject: Someone commented on doc
From: Berto (Google Docs) <comments-noreply@docs.google.com>
Date: Fri, 6 Mar 2026 10:00:00 +0000
To: jzarecki@redhat.com

---

Message ID: f2
Subject: Team update
From: AI Team <ai-ux-all@redhat.com>
Date: Fri, 6 Mar 2026 10:00:00 +0000
To: jzarecki@redhat.com

---

Message ID: f3
Subject: Real email
From: Jane Doe <jane@acme.com>
Date: Fri, 6 Mar 2026 10:00:00 +0000
To: jzarecki@redhat.com

---

Message ID: f4
Subject: Group discussion
From: Dev Group <dev-group@googlegroups.com>
Date: Fri, 6 Mar 2026 10:00:00 +0000
To: jzarecki@redhat.com"

FILTER_SQL=$(echo "$GMAIL_FILTER_INPUT" | LC_SELF_EMAIL=jzarecki@redhat.com python3 "$SCRIPT_DIR/parse-source.py" --source gmail --run-id 99 2>/dev/null)
FILTER_COUNT=$(echo "$FILTER_SQL" | grep -c "INSERT INTO sightings" || echo 0)
assert_eq "Gmail filters: only jane@acme.com passes (3 filtered)" "1" "$FILTER_COUNT"
FILTER_HAS_JANE=$(echo "$FILTER_SQL" | grep -c "jane@acme.com" || echo 0)
assert_eq "Gmail filters: jane@acme.com included" "1" "$FILTER_HAS_JANE"

echo ""

# ---------------------------------------------------------------
echo "16. Slack DM-first parser (new format)"
# ---------------------------------------------------------------
SLACK_DM_INPUT="===CHANNEL D001 (im)===
MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,BotName,FileCount,AttachmentIDs,HasMedia,Cursor
1234,U111,alice,Alice Smith,D001,,hello,2026-01-08T10:00:00Z,,,0,,false,
1235,U222,self,Self User,D001,,hi back,2026-01-08T10:01:00Z,,,0,,false,
===CHANNEL G002 (mpim)===
MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,BotName,FileCount,AttachmentIDs,HasMedia,Cursor
1236,U333,bob,Bob Jones,G002,,group msg,2026-01-08T11:00:00Z,,,0,,false,
1237,U444,,Bot Alert,G002,,alert,2026-01-08T11:01:00Z,,BotApp,0,,false,"

# Add self to cache so it gets filtered
run_sql "INSERT OR REPLACE INTO slack_users (slack_uid, username, real_name, email) VALUES ('U222', 'self', 'Self User', 'jzarecki@redhat.com');"
run_sql "INSERT OR REPLACE INTO slack_users (slack_uid, username, real_name, email) VALUES ('U111', 'alice', 'Alice Smith', 'alice@corp.com');"

SLACK_DM_SQL=$(echo "$SLACK_DM_INPUT" | LC_SELF_EMAIL=jzarecki@redhat.com python3 "$SCRIPT_DIR/parse-source.py" --source slack --run-id 99 --db-path "$TEST_DB" 2>/dev/null)
SLACK_DM_COUNT=$(echo "$SLACK_DM_SQL" | grep -c "INSERT INTO sightings" || echo 0)
assert_eq "Slack DM parser: 2 sightings (Alice DM + Bob MPDM, self filtered, bot filtered)" "2" "$SLACK_DM_COUNT"
SLACK_HAS_DM=$(echo "$SLACK_DM_SQL" | grep -c "slack_dm" || echo 0)
assert_eq "Slack DM parser: both are slack_dm type" "2" "$SLACK_HAS_DM"
SLACK_HAS_ALICE=$(echo "$SLACK_DM_SQL" | grep -c "U111" || echo 0)
assert_eq "Slack DM parser: Alice included" "1" "$SLACK_HAS_ALICE"

echo ""

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo "==========================================="
echo "  Results: $PASS passed, $FAIL failed"
echo "==========================================="

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
