#!/usr/bin/env bash
# tests/setup.sh -- Reset project to a clean testable state using fixture data.
# Safe to run repeatedly; always starts from a fresh DB.
#
# Usage: ./tests/setup.sh [--env-only]
#   --env-only  Only write .env, skip DB and fixture staging

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"

cd "$PROJECT_DIR"

# Safety check: warn if real data is present
PEOPLE_COUNT=$(sqlite3 data/contacts.db "SELECT COUNT(*) FROM people;" 2>/dev/null || echo 0)
if [ "$PEOPLE_COUNT" -gt 10 ]; then
    echo "  WARNING: data/contacts.db has $PEOPLE_COUNT real contacts."
    echo "  This will be DELETED and replaced with test fixture data."
    printf "  Continue? (yes/N) "
    read -r CONFIRM || CONFIRM=""  # read returns non-zero on EOF (non-TTY); treat as empty
    if [ "$CONFIRM" != "yes" ] && [ "${1:-}" != "--force" ]; then
        echo "  Aborted. Your real DB is untouched."
        exit 1
    fi
fi

echo "=== Groundwork Test Setup ==="
echo ""

# --- .env (always overwrite with test values, save original) ---
if [ -f ".env" ] && [ ! -f ".env.bak" ]; then
    cp .env .env.bak
    echo "  .env backed up to .env.bak"
fi
cat > .env <<'EOF'
LC_SELF_EMAIL=test@example.com
LC_PROVIDER=direct
LC_SLACK_WORKSPACE=testworkspace
LC_MAX_PARTICIPANTS=80
LC_COLLECT_DAYS=7
LC_ENRICH_BATCH_SIZE=5
EOF
echo "  .env written (LC_SELF_EMAIL=test@example.com)"

if [ "${1:-}" = "--env-only" ]; then
    echo ""
    echo "  Done (env only)."
    exit 0
fi

# --- Fresh database ---
mkdir -p data data/tmp data/imports data/.credentials
rm -f data/contacts.db
sqlite3 data/contacts.db < schema.sql
echo "  data/contacts.db created from schema.sql"

# Seed slack_users cache so self (U_SELF) is filtered during Slack parsing
sqlite3 data/contacts.db "
INSERT INTO slack_users (slack_uid, username, real_name, email)
VALUES ('U_SELF', 'testself', 'Test User', 'test@example.com');
INSERT INTO slack_users (slack_uid, username, real_name, email)
VALUES ('U_DAVE', 'dave', 'Dave Rogers', 'dave@startup.io');
INSERT INTO slack_users (slack_uid, username, real_name, email)
VALUES ('U_EVE', 'eve', 'Eve Chan', 'eve@agency.com');
"
echo "  Slack user cache seeded (3 users)"

# --- Stage fixtures ---
cp "$FIXTURES_DIR/lc_gmail.txt"    data/tmp/lc_gmail.txt
cp "$FIXTURES_DIR/lc_calendar.txt" data/tmp/lc_calendar.txt
cp "$FIXTURES_DIR/lc_slack.txt"    data/tmp/lc_slack.txt
echo "  Fixture files staged to data/tmp/"

# --- Seed run record ---
RUN_ID=$(sqlite3 data/contacts.db "
INSERT INTO runs (started_at, source, notes)
VALUES ('2026-04-07T08:00:00Z', 'all', 'test harness run');
SELECT last_insert_rowid();
")
echo "  Run record created: id=$RUN_ID"

# Store run_id for downstream scripts
echo "$RUN_ID" > data/tmp/test_run_id

echo ""
echo "  Setup complete. Run ID: $RUN_ID"
echo "  Next: ./scripts/process-run.sh $RUN_ID"
echo "   Then: ./tests/verify.sh"
echo ""
echo "  NOTE: .env was overwritten with test values."
echo "        Your original .env is backed up at .env.bak"
echo "        Restore with: cp .env.bak .env"
