#!/usr/bin/env bash
PROTECTED_FILES=(
  "SPEC.md"
  ".env"
  ".env.local"
  ".env.production"
  ".claude/settings.json"
  "schema.sql"
)

for file in "${PROTECTED_FILES[@]}"; do
  if echo "$CLAUDE_TOOL_INPUT" | grep -q "$file"; then
    echo "BLOCKED: $file is a protected file. Ask the user before editing."
    exit 1
  fi
done

exit 0
