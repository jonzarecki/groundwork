#!/usr/bin/env bash
set -euo pipefail

# auto-merge.sh -- Detect and merge unambiguous duplicates.
# Only merges when LOWER(name) matches exactly AND company_domain matches.
# Flags ambiguous cases for agent review.
#
# Usage: ./scripts/auto-merge.sh [--dry-run]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/contacts.db"

DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then DRY_RUN=true; fi

if [ ! -f "$DB_PATH" ]; then
  echo "No database at $DB_PATH"
  exit 1
fi

PAIRS=$(sqlite3 -separator '|' "$DB_PATH" "
  SELECT a.id, a.name, a.email, a.interaction_score,
         b.id, b.name, b.email, b.interaction_score
  FROM people a JOIN people b ON a.id < b.id
  WHERE LOWER(a.name) = LOWER(b.name)
    AND a.company_domain = b.company_domain
    AND a.company_domain IS NOT NULL
    AND a.status != 'ignored' AND b.status != 'ignored'
  ORDER BY a.interaction_score DESC;
")

if [ -z "$PAIRS" ]; then
  echo "No obvious duplicates found."
  exit 0
fi

MERGED=0
while IFS='|' read -r a_id a_name a_email a_score b_id b_name b_email b_score; do
  if [ "$a_score" -ge "$b_score" ]; then
    KEEP_ID="$a_id"; MERGE_ID="$b_id"
    KEEP_NAME="$a_name"; MERGE_NAME="$b_name"
  else
    KEEP_ID="$b_id"; MERGE_ID="$a_id"
    KEEP_NAME="$b_name"; MERGE_NAME="$a_name"
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "  Would merge: #$MERGE_ID ($MERGE_NAME) -> #$KEEP_ID ($KEEP_NAME)"
  else
    if "$SCRIPT_DIR/merge-people.sh" --keep "$KEEP_ID" --merge "$MERGE_ID" \
        --reason "Auto-merge: exact name + domain match" 2>/dev/null; then
      echo "  Merged: #$MERGE_ID -> #$KEEP_ID ($KEEP_NAME)"
      MERGED=$((MERGED + 1))
    else
      echo "  Skipped: #$MERGE_ID or #$KEEP_ID not found"
    fi
  fi
done <<< "$PAIRS"

if [ "$DRY_RUN" = true ]; then
  COUNT=$(echo "$PAIRS" | wc -l | tr -d ' ')
  echo "Dry run: $COUNT pairs would be merged."
else
  echo "Auto-merge complete ($MERGED merged)."
fi
