#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"
SCHEMA_PATH="$PROJECT_DIR/schema.sql"

echo "Groundwork Setup"
echo "======================"
echo ""

# --- Step 1: Init database ---
echo "1. Database"
echo "───────────"
mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/data/tmp" "$PROJECT_DIR/data/imports"

if [ -f "$DB_PATH" ]; then
    echo "   Already exists at $DB_PATH"
else
    sqlite3 "$DB_PATH" < "$SCHEMA_PATH"
    echo "   Created at $DB_PATH"
fi

# Verify core tables (sightings, not the old 'interactions' name)
TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('people','sightings','runs');")
if [ "$TABLE_COUNT" -eq 3 ]; then
    echo "   ✓ Database tables verified (people, sightings, runs)"
else
    echo "   ✗ Database missing tables (found $TABLE_COUNT/3 core tables)"
    echo "   Re-run: sqlite3 $DB_PATH < $SCHEMA_PATH"
fi
echo ""

# --- Step 2: .env check ---
echo "2. Configuration (.env)"
echo "────────────────────────"

ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    echo "   Created .env from .env.example"
    echo "   ACTION REQUIRED: Edit .env and set LC_SELF_EMAIL=you@example.com"
fi

SELF_EMAIL=$(grep -E "^LC_SELF_EMAIL=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)
PROVIDER=$(grep -E "^LC_PROVIDER=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' "' || echo "direct")
SLACK_WS=$(grep -E "^LC_SLACK_WORKSPACE=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' "' || true)

if [ -z "$SELF_EMAIL" ] || [ "$SELF_EMAIL" = "you@example.com" ]; then
    echo "   ✗ LC_SELF_EMAIL not set -- edit .env before collecting"
else
    echo "   ✓ LC_SELF_EMAIL=$SELF_EMAIL"
fi
echo "   LC_PROVIDER=$PROVIDER"
echo ""

# --- Step 3: Provider-specific setup ---
if [ "$PROVIDER" = "direct" ] || [ -z "$PROVIDER" ]; then
    echo "3. Direct Provider (Google OAuth + Slack/LinkedIn Chrome cookies)"
    echo "──────────────────────────────────────────────────────────────────"

    CREDS_DIR="$PROJECT_DIR/data/.credentials"
    ERRORS_DIRECT=0

    # Google credentials
    if [ -f "$CREDS_DIR/google.json" ]; then
        echo "   ✓ Google credentials found ($CREDS_DIR/google.json)"
    else
        echo "   ✗ Google credentials not set up"
        ERRORS_DIRECT=$((ERRORS_DIRECT + 1))
    fi

    # Slack credentials (optional)
    if [ -f "$CREDS_DIR/slack.json" ]; then
        AGE_DAYS=$(python3 -c "
import json, time
d = json.load(open('$CREDS_DIR/slack.json'))
print(f'{(time.time() - d.get(\"extracted_at\", 0)) / 86400:.0f}')
" 2>/dev/null || echo "?")
        if [ "$AGE_DAYS" = "?" ] || [ "$AGE_DAYS" -gt 13 ] 2>/dev/null; then
            echo "   ⚠ Slack credentials may be expired (${AGE_DAYS}d old) -- re-run setup-auth.py slack"
        else
            echo "   ✓ Slack credentials found (${AGE_DAYS}d old)"
        fi
    else
        if [ -n "$SLACK_WS" ] && [ "$SLACK_WS" != "mycompany" ]; then
            echo "   ⊘ Slack credentials not set up (optional -- run: python3 scripts/setup-auth.py slack)"
        else
            echo "   ⊘ Slack not configured (optional -- set LC_SLACK_WORKSPACE in .env, then: python3 scripts/setup-auth.py slack)"
        fi
    fi

    # LinkedIn credentials (optional)
    if [ -f "$CREDS_DIR/linkedin.json" ]; then
        echo "   ✓ LinkedIn credentials found"
    else
        echo "   ⊘ LinkedIn credentials not set up (optional -- run: python3 scripts/setup-auth.py linkedin)"
    fi

    if [ "$ERRORS_DIRECT" -gt 0 ]; then
        echo ""
        echo "   Run to complete setup:"
        echo "     python3 scripts/setup-auth.py google"
        echo ""
        echo "   Requirements: pip install google-api-python-client google-auth-oauthlib"
    fi
    echo ""

else
    echo "3. MCP Provider (legacy Docker stack)"
    echo "──────────────────────────────────────"

    CLAUDE_MCP="$PROJECT_DIR/.claude/mcp.json"
    CURSOR_MCP="$PROJECT_DIR/.cursor/mcp.json"

    if [ -f "$CLAUDE_MCP" ]; then
        echo "   ✓ .claude/mcp.json exists"
    else
        echo "   ✗ .claude/mcp.json not found"
    fi

    # MCP liveness check
    MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup-check","version":"1.0"}}}'
    MCP_PROXY="${MCP_PROXY_URL:-http://localhost:9090}"

    for SERVER in google-workspace google-contacts slack; do
        URL="$MCP_PROXY/$SERVER/sse"
        RESP=$(curl -s -m 5 "$URL" 2>&1 || true)
        if echo "$RESP" | grep -q "data:" 2>/dev/null; then
            echo "   ✓ $SERVER reachable at $URL"
        else
            echo "   ✗ $SERVER not responding at $URL"
        fi
    done

    echo ""
    echo "   To refresh tokens: ./scripts/setup-auth.sh"
    echo ""
fi

# --- Step 4: Python dependencies ---
echo "4. Python dependencies"
echo "──────────────────────"

# Find python3 without hardcoded paths
PYTHON3=$(command -v python3 || command -v python || echo "")
if [ -z "$PYTHON3" ]; then
    echo "   ✗ python3 not found -- install Python 3.9+"
else
    PY_VER=$("$PYTHON3" --version 2>&1 | awk '{print $2}')
    echo "   ✓ python3 found: $PYTHON3 ($PY_VER)"

    if [ "$PROVIDER" = "direct" ] || [ -z "$PROVIDER" ]; then
        MISSING_DEPS=""
        for PKG in "google.oauth2" "googleapiclient" "pycookiecheat" "requests"; do
            if ! "$PYTHON3" -c "import $PKG" 2>/dev/null; then
                MISSING_DEPS="$MISSING_DEPS $PKG"
            fi
        done
        if [ -z "$MISSING_DEPS" ]; then
            echo "   ✓ Direct provider dependencies installed"
        else
            echo "   ✗ Missing dependencies:$MISSING_DEPS"
            echo "   Install: pip install google-api-python-client google-auth-oauthlib pycookiecheat requests"
        fi
    else
        if "$PYTHON3" -c "from mcp.client.sse import sse_client" 2>/dev/null; then
            MCP_VER=$("$PYTHON3" -c "import importlib.metadata; print(importlib.metadata.version('mcp'))" 2>/dev/null || echo "?")
            echo "   ✓ MCP Python SDK v$MCP_VER installed"
        else
            echo "   ✗ MCP Python SDK not found"
            echo "   Install: pip install mcp"
        fi
    fi
fi
echo ""

# --- Step 5: LinkedIn connections import ---
echo "5. LinkedIn connections"
echo "───────────────────────"

LI_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM linkedin_connections;" 2>/dev/null || echo 0)
if [ "$LI_COUNT" -gt 0 ]; then
    echo "   ✓ $LI_COUNT LinkedIn connections already imported"
else
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
        echo "   ⊘ No LinkedIn connections imported yet (optional)"
        echo "   To import: export from linkedin.com > Me > Settings > Data privacy > Get a copy"
        echo "   Then run:  ./scripts/import-connections.sh ~/Downloads/Connections.csv"
    fi
fi
echo ""

# --- Summary ---
echo "═══════════════════════"
echo "Setup complete."
echo ""
if [ "$PROVIDER" = "direct" ] || [ -z "$PROVIDER" ]; then
    echo "Next steps:"
    echo "  1. Run: python3 scripts/setup-auth.py   (one-time auth setup)"
    echo "  2. Run: ./scripts/run-collect.sh         (collect contacts)"
else
    echo "Next steps:"
    echo "  1. Restart Cursor to pick up MCP server config"
    echo "  2. Run: ./scripts/run-collect.sh --provider mcp"
fi
