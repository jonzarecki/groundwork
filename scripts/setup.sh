#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"
SCHEMA_PATH="$PROJECT_DIR/schema.sql"
CLAUDE_MCP="$PROJECT_DIR/.claude/mcp.json"
CURSOR_MCP="$PROJECT_DIR/.cursor/mcp.json"

echo "Linked Collector Setup"
echo "======================"
echo ""

# --- Step 1: Init database ---
echo "1. Database"
echo "───────────"
mkdir -p "$PROJECT_DIR/data"

if [ -f "$DB_PATH" ]; then
    echo "   Already exists at $DB_PATH"
else
    sqlite3 "$DB_PATH" < "$SCHEMA_PATH"
    echo "   Created at $DB_PATH"
fi
echo ""

# --- Step 2: Create .cursor/mcp.json ---
echo "2. MCP servers"
echo "──────────────"
mkdir -p "$PROJECT_DIR/.cursor"

if ! command -v jq &>/dev/null; then
    echo "   ERROR: jq is required but not installed."
    echo "   Install with: brew install jq"
    exit 1
fi

SLACK_TOKEN=""
if [ -f "$CLAUDE_MCP" ]; then
    SLACK_TOKEN=$(jq -r '.mcpServers["slack-mcp"].headers.Authorization // empty' "$CLAUDE_MCP")
fi

if [ -z "$SLACK_TOKEN" ]; then
    SLACK_TOKEN="Bearer <your-slack-token>"
    echo "   WARNING: Could not read Slack token from .claude/mcp.json"
    echo "   You'll need to edit .cursor/mcp.json with your Slack auth token."
fi

NEW_SERVERS=$(cat <<EOF
{
  "google-workspace": {
    "type": "streamableHttp",
    "url": "http://localhost:8000/mcp"
  },
  "google-contacts": {
    "type": "streamableHttp",
    "url": "http://localhost:8082/mcp"
  },
  "slack-mcp": {
    "type": "streamableHttp",
    "url": "http://localhost:13070/mcp",
    "headers": {
      "Authorization": "$SLACK_TOKEN"
    }
  }
}
EOF
)

if [ -f "$CURSOR_MCP" ]; then
    EXISTING=$(cat "$CURSOR_MCP")
    MERGED=$(echo "$EXISTING" | jq --argjson new "$NEW_SERVERS" '.mcpServers += $new')
    echo "$MERGED" > "$CURSOR_MCP"
    echo "   Merged MCP servers into existing $CURSOR_MCP"
else
    echo "{\"mcpServers\": $NEW_SERVERS}" | jq '.' > "$CURSOR_MCP"
    echo "   Created $CURSOR_MCP"
fi
echo ""

# --- Step 3: Verify config ---
echo "3. Config verification"
echo "──────────────────────"

ERRORS=0
WARNINGS=0

TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('people','interactions','runs');")
if [ "$TABLE_COUNT" -eq 3 ]; then
    echo "   ✓ Database has all 3 tables (people, interactions, runs)"
else
    echo "   ✗ Database missing tables (found $TABLE_COUNT/3)"
    ERRORS=$((ERRORS + 1))
fi

INDEX_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%';")
if [ "$INDEX_COUNT" -ge 7 ]; then
    echo "   ✓ Database has $INDEX_COUNT indexes"
else
    echo "   ✗ Database indexes incomplete ($INDEX_COUNT, expected 7+)"
    ERRORS=$((ERRORS + 1))
fi

if [ -f "$CLAUDE_MCP" ]; then
    echo "   ✓ .claude/mcp.json exists"
    for SERVER in google-workspace google-contacts slack-mcp; do
        if jq -e ".mcpServers[\"$SERVER\"]" "$CLAUDE_MCP" &>/dev/null; then
            echo "   ✓ $SERVER configured in .claude/mcp.json"
        else
            echo "   ✗ $SERVER missing from .claude/mcp.json"
            ERRORS=$((ERRORS + 1))
        fi
    done
else
    echo "   ✗ .claude/mcp.json not found"
    ERRORS=$((ERRORS + 1))
fi

for SERVER in google-workspace google-contacts slack-mcp; do
    if jq -e ".mcpServers[\"$SERVER\"]" "$CURSOR_MCP" &>/dev/null; then
        echo "   ✓ $SERVER configured in .cursor/mcp.json"
    else
        echo "   ✗ $SERVER missing from .cursor/mcp.json"
        ERRORS=$((ERRORS + 1))
    fi
done
echo ""

# --- Step 4: MCP server liveness ---
echo "4. MCP server liveness"
echo "──────────────────────"

MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup-check","version":"1.0"}}}'
MCP_HEADERS='-H "Content-Type: application/json" -H "Accept: application/json, text/event-stream"'

check_mcp_server() {
    local name="$1" url="$2" extra_headers="${3:-}"
    local response
    response=$(curl -s -m 5 -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        $extra_headers \
        -d "$MCP_INIT" 2>&1) || true

    if echo "$response" | grep -q '"serverInfo"'; then
        local server_name server_version
        server_name=$(echo "$response" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)
        server_version=$(echo "$response" | grep -o '"version":"[^"]*"' | head -1 | cut -d'"' -f4)
        echo "   ✓ $name is running ($server_name v$server_version)"
        return 0
    elif echo "$response" | grep -qi 'connection refused\|could not connect\|timed out'; then
        echo "   ✗ $name is NOT running at $url"
        return 1
    else
        echo "   ? $name responded but may have issues"
        echo "     Response: $(echo "$response" | head -c 120)"
        return 1
    fi
}

check_mcp_server "google-workspace" "http://localhost:8000/mcp" "" || ERRORS=$((ERRORS + 1))
check_mcp_server "google-contacts"  "http://localhost:8082/mcp" "" || ERRORS=$((ERRORS + 1))

SLACK_AUTH_HEADER=""
if [ -n "$SLACK_TOKEN" ] && [ "$SLACK_TOKEN" != "Bearer <your-slack-token>" ]; then
    SLACK_AUTH_HEADER="-H \"Authorization: $SLACK_TOKEN\""
fi
check_mcp_server "slack-mcp" "http://localhost:13070/mcp" "$SLACK_AUTH_HEADER" || ERRORS=$((ERRORS + 1))
echo ""

# --- Step 5: Google auth check ---
echo "5. Google auth check"
echo "────────────────────"

GW_HEADER_FILE=$(mktemp)
GW_INIT_RESPONSE=$(curl -s -m 5 -D "$GW_HEADER_FILE" -X POST "http://localhost:8000/mcp" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d "$MCP_INIT" 2>&1) || true

GOOGLE_EMAIL=$(echo "$GW_INIT_RESPONSE" | grep -o 'Connected Google account: [^ \\]*' | head -1 | sed 's/Connected Google account: //')

if [ -n "$GOOGLE_EMAIL" ]; then
    echo "   ✓ Google authenticated as $GOOGLE_EMAIL"

    SESSION_ID=$(grep -oi 'mcp-session-id: [a-f0-9]*' "$GW_HEADER_FILE" | head -1 | sed 's/mcp-session-id: //i')

    if [ -n "$SESSION_ID" ]; then
        GMAIL_CHECK=$(curl -s -m 10 -X POST "http://localhost:8000/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -H "Mcp-Session-Id: $SESSION_ID" \
            -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"search_gmail_messages\",\"arguments\":{\"query\":\"newer_than:1d\",\"user_google_email\":\"$GOOGLE_EMAIL\",\"page_size\":1}}}" 2>&1) || true

        if echo "$GMAIL_CHECK" | grep -q 'Message ID'; then
            echo "   ✓ Gmail access works"
        elif echo "$GMAIL_CHECK" | grep -qi 'error\|auth'; then
            echo "   ✗ Gmail access failed (may need re-auth)"
            WARNINGS=$((WARNINGS + 1))
        else
            echo "   ? Gmail returned unexpected response"
            WARNINGS=$((WARNINGS + 1))
        fi

        TIME_MIN=$(date -u -v-1d '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '1 day ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo "")
        if [ -n "$TIME_MIN" ]; then
            CAL_CHECK=$(curl -s -m 10 -X POST "http://localhost:8000/mcp" \
                -H "Content-Type: application/json" \
                -H "Accept: application/json, text/event-stream" \
                -H "Mcp-Session-Id: $SESSION_ID" \
                -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"get_events\",\"arguments\":{\"user_google_email\":\"$GOOGLE_EMAIL\",\"time_min\":\"$TIME_MIN\",\"max_results\":1}}}" 2>&1) || true

            if echo "$CAL_CHECK" | grep -qi 'retrieved\|events\|Starts:'; then
                echo "   ✓ Calendar access works"
            elif echo "$CAL_CHECK" | grep -qi 'error\|auth'; then
                echo "   ✗ Calendar access failed (may need re-auth)"
                WARNINGS=$((WARNINGS + 1))
            else
                echo "   ? Calendar returned unexpected response"
                WARNINGS=$((WARNINGS + 1))
            fi
        fi
    else
        echo "   ? Could not establish session for tool checks"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "   ✗ Google not authenticated (run start_google_auth in a session)"
    WARNINGS=$((WARNINGS + 1))
fi
rm -f "$GW_HEADER_FILE"
echo ""

# --- Step 6: Slack auth check ---
echo "6. Slack auth check"
echo "───────────────────"

if [ -n "$SLACK_TOKEN" ] && [ "$SLACK_TOKEN" != "Bearer <your-slack-token>" ]; then
    SLACK_HEADER_FILE=$(mktemp)
    SLACK_INIT_RESPONSE=$(curl -s -m 5 -D "$SLACK_HEADER_FILE" -X POST "http://localhost:13070/mcp" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Authorization: $SLACK_TOKEN" \
        -d "$MCP_INIT" 2>&1) || true

    SLACK_SESSION=$(grep -i 'mcp-session-id:' "$SLACK_HEADER_FILE" | head -1 | sed 's/.*mcp-session-id: *//i' | tr -d '\r')
    rm -f "$SLACK_HEADER_FILE"

    if [ -n "$SLACK_SESSION" ]; then
        curl -s -m 3 -X POST "http://localhost:13070/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -H "Authorization: $SLACK_TOKEN" \
            -H "Mcp-Session-Id: $SLACK_SESSION" \
            -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null 2>&1 || true

        SLACK_CHECK=$(curl -s -m 10 -X POST "http://localhost:13070/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -H "Authorization: $SLACK_TOKEN" \
            -H "Mcp-Session-Id: $SLACK_SESSION" \
            -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"conversations_search_messages","arguments":{"filter_date_after":"'"$(date -u '+%Y-%m-%d')"'","limit":1}}}' 2>&1) || true

        if echo "$SLACK_CHECK" | grep -q 'MsgID\|UserID\|Channel'; then
            echo "   ✓ Slack access works (messages readable)"
        elif echo "$SLACK_CHECK" | grep -qi 'error'; then
            echo "   ✗ Slack access failed: $(echo "$SLACK_CHECK" | grep -o '"message":"[^"]*"' | head -1)"
            WARNINGS=$((WARNINGS + 1))
        else
            echo "   ? Slack returned unexpected response"
            WARNINGS=$((WARNINGS + 1))
        fi
    else
        echo "   ✗ Could not establish Slack session"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "   ⊘ Slack token not configured, skipping auth check"
    WARNINGS=$((WARNINGS + 1))
fi
echo ""

# --- Step 7: LinkedIn MCP server ---
echo "7. LinkedIn MCP"
echo "────────────────"

if command -v uvx &>/dev/null; then
    if uvx linkedin-scraper-mcp --status &>/dev/null 2>&1; then
        echo "   ✓ linkedin-scraper-mcp installed with active session"
    else
        echo "   ⊘ linkedin-scraper-mcp session not active"
        echo "   To set up (one-time):"
        echo "     uvx patchright install chromium"
        echo "     uvx linkedin-scraper-mcp --login"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "   ⊘ uvx not found -- install uv to use linkedin-scraper-mcp"
    echo "   Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    WARNINGS=$((WARNINGS + 1))
fi
echo ""

# --- Step 8: LinkedIn connections import ---
echo "8. LinkedIn connections"
echo "───────────────────────"

# Run migration if table doesn't exist yet
sqlite3 "$DB_PATH" "
CREATE TABLE IF NOT EXISTS linkedin_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    linkedin_url TEXT UNIQUE,
    email TEXT,
    company TEXT,
    position TEXT,
    connected_on TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_linkedin_connections_url ON linkedin_connections(linkedin_url);
CREATE INDEX IF NOT EXISTS idx_linkedin_connections_email ON linkedin_connections(email);
" 2>/dev/null

LI_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM linkedin_connections;")
if [ "$LI_COUNT" -gt 0 ]; then
    echo "   ✓ $LI_COUNT LinkedIn connections already imported"
else
    # Check common locations for the CSV
    LI_CSV=""
    for CANDIDATE in "$PROJECT_DIR/data/Connections.csv" "$HOME/Downloads/Connections.csv"; do
        if [ -f "$CANDIDATE" ]; then
            LI_CSV="$CANDIDATE"
            break
        fi
    done

    if [ -n "$LI_CSV" ]; then
        echo "   Found Connections.csv at $LI_CSV -- importing..."
        "$SCRIPT_DIR/import-connections.sh" "$LI_CSV"
    else
        echo "   ⊘ No LinkedIn connections imported yet"
        echo ""
        echo "   To import your connections:"
        echo "   1. Open https://www.linkedin.com/mypreferences/d/download-my-data"
        echo "   2. Select 'Connections' and click 'Request archive'"
        echo "   3. Wait for LinkedIn's email (5-15 min), download ZIP"
        echo "   4. Extract Connections.csv and either:"
        echo "      - Drop it into ./data/Connections.csv and re-run setup"
        echo "      - Run: ./scripts/import-connections.sh ~/Downloads/Connections.csv"
        WARNINGS=$((WARNINGS + 1))
    fi
fi
echo ""

# --- Summary ---
echo "═══════════════════════"
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo "Setup complete. All checks passed."
elif [ "$ERRORS" -eq 0 ]; then
    echo "Setup complete with $WARNINGS warning(s)."
    echo "Warnings are non-blocking -- collection will skip unavailable sources."
else
    echo "Setup finished with $ERRORS error(s) and $WARNINGS warning(s)."
fi

echo ""
echo "Next steps:"
echo "  1. Restart Cursor to pick up new MCP servers"
echo "  2. Ask the agent to 'collect' or 'run' to start gathering contacts"
