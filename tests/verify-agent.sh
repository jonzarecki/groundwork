#!/usr/bin/env bash
# tests/verify-agent.sh -- Verify agent behavior after a collect run.
# Checks both DB state (via verify.sh) AND the latest agent transcript.
#
# Usage: ./tests/verify-agent.sh [transcript_path]
#   transcript_path: path to a specific .jsonl transcript, or omits to auto-find latest

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TRANSCRIPTS_DIR="$HOME/.cursor/projects/$(echo "$PROJECT_DIR" | sed 's|/|-|g; s|^-||')/agent-transcripts"

PASS=0
FAIL=0
WARN=0

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

warn() {
    WARN=$((WARN + 1))
    echo "  WARN: $1"
}

echo "=== Groundwork Agent Behavior Verify ==="
echo ""

# -------------------------------------------------------------------
echo "1. DB state (via verify.sh)"
# -------------------------------------------------------------------
if "$SCRIPT_DIR/verify.sh" "$PROJECT_DIR/data/contacts.db"; then
    echo "  DB state: OK"
else
    echo "  DB state: FAILED (see above)"
    FAIL=$((FAIL + 1))
fi

# -------------------------------------------------------------------
echo ""
echo "2. Transcript analysis"
# -------------------------------------------------------------------
TRANSCRIPT="${1:-}"

if [ -z "$TRANSCRIPT" ]; then
    # Find the most recently modified transcript
    if [ -d "$TRANSCRIPTS_DIR" ]; then
        TRANSCRIPT=$(find "$TRANSCRIPTS_DIR" -name "*.jsonl" -newer "$PROJECT_DIR/data/contacts.db" 2>/dev/null \
            | xargs ls -t 2>/dev/null | head -1 || true)
    fi
fi

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    warn "No transcript found -- skipping trajectory checks"
    warn "  Run the agent first, then re-run this script"
    echo ""
    echo "==========================================="
    echo "  Results: $PASS passed, $FAIL failed, $WARN warnings"
    echo "==========================================="
    exit 0
fi

echo "  Transcript: $TRANSCRIPT"
echo ""

# Extract all assistant text and tool calls from the JSONL
TRANSCRIPT_TEXT=$(python3 -c "
import json, sys
lines = []
for line in open('$TRANSCRIPT'):
    try:
        obj = json.loads(line)
        role = obj.get('role', '')
        content = obj.get('content', '')
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get('type') == 'text':
                        lines.append(block.get('text', ''))
                    elif block.get('type') == 'tool_use':
                        lines.append('TOOL_CALL:' + block.get('name', '') + ':' + json.dumps(block.get('input', {})))
        elif isinstance(content, str):
            lines.append(content)
    except Exception:
        pass
print('\n'.join(lines))
" 2>/dev/null || echo "")

if [ -z "$TRANSCRIPT_TEXT" ]; then
    warn "Could not parse transcript -- may be empty or wrong format"
    echo ""
    echo "==========================================="
    echo "  Results: $PASS passed, $FAIL failed, $WARN warnings"
    echo "==========================================="
    exit 0
fi

# Check: agent ran run-collect.sh (not raw MCP tool calls)
RAN_COLLECT=$(echo "$TRANSCRIPT_TEXT" | grep -c "run-collect.sh" || echo 0)
assert_eq "agent ran run-collect.sh" "1" "$([ "$RAN_COLLECT" -ge 1 ] && echo 1 || echo 0)"

# Negative check: agent did NOT call MCP tools directly for collection
# (collect-sources.py should do that, not the agent)
MCP_GMAIL_CALLS=$(echo "$TRANSCRIPT_TEXT" | grep -c "TOOL_CALL:get_gmail\|TOOL_CALL:search_gmail" || echo 0)
assert_eq "agent did not call Gmail MCP directly" "0" "$([ "$MCP_GMAIL_CALLS" -gt 0 ] && echo 1 || echo 0)"

MCP_CAL_CALLS=$(echo "$TRANSCRIPT_TEXT" | grep -c "TOOL_CALL:get_calendar\|TOOL_CALL:list_events" || echo 0)
assert_eq "agent did not call Calendar MCP directly" "0" "$([ "$MCP_CAL_CALLS" -gt 0 ] && echo 1 || echo 0)"

# Check: agent showed output to user (should have quoted the report or shown key lines)
SHOWED_REPORT=$(echo "$TRANSCRIPT_TEXT" | grep -c "=== Collect Report\|New contacts:\|Sightings:" || echo 0)
assert_eq "agent showed collect report output" "1" "$([ "$SHOWED_REPORT" -ge 1 ] && echo 1 || echo 0)"

# Check: if there were flagged items, agent addressed them; if none, agent declared done
FLAGGED_IN_DB=$(sqlite3 "$PROJECT_DIR/data/contacts.db" "
SELECT (
  SELECT COUNT(*) FROM sightings WHERE person_id IS NULL
) + (
  SELECT COUNT(*) FROM (
    SELECT a.id FROM people a JOIN people b ON a.id < b.id
    WHERE LOWER(a.name) = LOWER(b.name) AND a.company_domain = b.company_domain
      AND a.status != 'ignored' AND b.status != 'ignored'
  )
);" 2>/dev/null || echo "0")

if [ "$FLAGGED_IN_DB" = "0" ]; then
    # No flags: agent should not have done extra unnecessary work
    EXTRA_MERGE=$(echo "$TRANSCRIPT_TEXT" | grep -c "merge-people.sh" || echo 0)
    if [ "$EXTRA_MERGE" -gt 2 ]; then
        warn "Agent called merge-people.sh multiple times with no flagged duplicates"
    else
        PASS=$((PASS + 1))
        echo "  PASS: agent completed without unnecessary merges (no flags)"
    fi
fi

# Check: if LinkedIn enrichment was run, correct ordering was used
LI_ENRICHED=$(sqlite3 "$PROJECT_DIR/data/contacts.db" "SELECT COUNT(*) FROM linkedin_searches;" 2>/dev/null || echo 0)
if [ "$LI_ENRICHED" -gt 0 ]; then
    # Verify enrichment happened in score order by checking linkedin_searches order vs interaction_scores
    echo "  INFO: LinkedIn enrichment was run ($LI_ENRICHED searches logged)"

    # The first person enriched should have the highest score among all enriched people
    FIRST_ENRICHED_SCORE=$(sqlite3 "$PROJECT_DIR/data/contacts.db" "
        SELECT p.interaction_score
        FROM linkedin_searches ls
        JOIN people p ON p.id = ls.person_id
        ORDER BY ls.searched_at ASC
        LIMIT 1;" 2>/dev/null || echo "")
    MAX_ENRICHED_SCORE=$(sqlite3 "$PROJECT_DIR/data/contacts.db" "
        SELECT MAX(p.interaction_score)
        FROM linkedin_searches ls
        JOIN people p ON p.id = ls.person_id;" 2>/dev/null || echo "")
    if [ -n "$FIRST_ENRICHED_SCORE" ] && [ -n "$MAX_ENRICHED_SCORE" ]; then
        assert_eq "linkedin enrichment started with highest-scored contact" "$MAX_ENRICHED_SCORE" "$FIRST_ENRICHED_SCORE"
    fi
fi

# -------------------------------------------------------------------
echo ""
echo "==========================================="
echo "  Results: $PASS passed, $FAIL failed, $WARN warnings"
echo "==========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
