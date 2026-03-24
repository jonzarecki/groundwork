#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CORE_SCRIPT="$PROJECT_DIR/../local-automation-mcp/auth_setup.py"
CORE_PROJECT="$PROJECT_DIR/../local-automation-mcp"

if [ ! -f "$CORE_SCRIPT" ]; then
    echo "Error: auth_setup.py not found at $CORE_SCRIPT"
    echo "Expected local-automation-mcp to be a sibling directory of linked-collector."
    exit 1
fi

# For linked-collector, write Slack/Google creds to the project .env
# (the Docker stack uses mcp-secrets.env, but linked-collector's
# collect-sources.py may read tokens from .env for direct API mode)
ENV_FILE="$PROJECT_DIR/.env"

# Also update the Docker stack's env file so containers get the tokens too
DOCKER_ENV="$CORE_PROJECT/mcp-secrets.env"

echo "Linked Collector - Auth Setup"
echo "=============================="

# Run against the Docker env file (primary)
uv run --project "$CORE_PROJECT" python "$CORE_SCRIPT" --env-file "$DOCKER_ENV" "$@"

# If not --check, also restart Docker containers via the main wrapper
if [[ " $* " != *" --check "* ]]; then
    echo ""
    echo "Restarting MCP containers..."

    CONTAINERS_TO_RESTART=()
    if [[ " $* " == *" --slack "* ]] || [[ $# -eq 0 ]]; then
        CONTAINERS_TO_RESTART+=(slack)
    fi
    if [[ " $* " == *" --google "* ]] || [[ $# -eq 0 ]]; then
        CONTAINERS_TO_RESTART+=(google-workspace google-contacts)
    fi

    if [ ${#CONTAINERS_TO_RESTART[@]} -gt 0 ]; then
        cd "$CORE_PROJECT"
        docker compose restart "${CONTAINERS_TO_RESTART[@]}" 2>/dev/null || \
            docker-compose restart "${CONTAINERS_TO_RESTART[@]}" 2>/dev/null || \
            echo "  Could not restart containers automatically."
    fi
fi
