Collect contacts from Gmail, Calendar, and Slack for the last $ARGUMENTS days (default: 7).

## Instructions

You are collecting contacts from communication channels and storing them in a SQLite database at `./data/contacts.db`. Read `CLAUDE.md` for project rules and `SPEC.md` for the full data model.

### Pre-flight

1. Check that `./data/contacts.db` exists. If not, create it by running: `sqlite3 ./data/contacts.db < schema.sql`
2. `source .env 2>/dev/null`
3. Create a run record:
   ```bash
   sqlite3 data/contacts.db "INSERT INTO runs (started_at, source) VALUES (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'all'); SELECT last_insert_rowid();"
   ```
   Note the run ID for Step 3.

### Step 1: Collect from all sources (parallel)

Call all three MCP sources **in parallel** (use parallel tool calls in a single message). Save each response to a temp file.

**Gmail** -- google-workspace MCP:
1. `search_gmail_messages(query="newer_than:Nd", user_google_email="jzarecki@redhat.com", page_size=25)` -- paginate with `page_token`
2. `get_gmail_messages_content_batch(message_ids=[...], user_google_email="jzarecki@redhat.com", format="metadata")` -- max 25 per batch
3. Save the full metadata output to `/tmp/lc_gmail.txt`

**Calendar** -- google-workspace MCP:
1. `get_events(user_google_email="jzarecki@redhat.com", time_min="<N days ago RFC3339>", time_max="<now RFC3339>", max_results=50, detailed=true)`
2. Save the full output to `/tmp/lc_calendar.txt`

**Slack** -- slack MCP:
1. `conversations_search_messages(filter_date_after="<N days ago YYYY-MM-DD>", limit=100)` -- paginate with `cursor`
2. Save the full output to `/tmp/lc_slack.txt`

### Step 2: Slack user lookups (cache misses only)

The parser uses a `slack_users` cache table. Most Slack UIDs are already cached from prior runs.

Run a dry parse to find cache misses:
```bash
python3 scripts/parse-source.py --source slack --run-id <RUN_ID> < /tmp/lc_slack.txt 2>&1 >/dev/null | grep "Cache misses"
```

If there are cache misses, call `users_search(query="<username>")` for each missed UID. Append the results to `/tmp/lc_slack.txt` separated by a `---` line:
```
<original slack output>
---
UserID,UserName,RealName,DisplayName,Email,Title,DMChannelID
U12345,jsmith,John Smith,jsmith,jsmith@corp.com,Engineer,
```

### Step 3: Process everything (one command)

```bash
./scripts/process-run.sh <RUN_ID> /tmp/lc_gmail.txt /tmp/lc_calendar.txt /tmp/lc_slack.txt
```

This runs deterministically: parse all sources -> resolve sightings (B1-B5 + auto-connect from linkedin_connections) -> update people -> finalize run.

### Step 4: Agent Review (only if needed)

Check the output of Step 3 for "B4 candidates for agent review". If there are unresolved sightings with fuzzy matches:
- Review each candidate pair
- If they match: `INSERT INTO matching_rules ...` with reasoning, then re-run `sqlite3 data/contacts.db < scripts/resolve-sightings.sql`
- If not: they'll be created as new people on the next run

**Merge duplicates** if found:
```bash
./scripts/merge-people.sh --keep <id> --merge <id> --reason "explanation"
```

### Step 5: Summary

Print the output from `process-run.sh` which includes resolution summary, people updated, and run stats.

### Important

- All filtering (self, bots, mailing lists, calendar invites, `LC_MAX_PARTICIPANTS`) is handled by `parse-source.py` -- do not filter manually.
- All resolution (B1-B5) is handled by `resolve-sightings.sql` -- do not write resolution SQL manually.
- All scoring/updates are handled by `update-people.sql` -- do not recalculate scores manually.
- The agent's only judgment call is B4 fuzzy matching and merge review.
- Never store email body content or Slack message text -- metadata only.
- If a source MCP fails, skip it and note in the run log.
