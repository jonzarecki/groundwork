Collect contacts from Gmail, Calendar, and Slack for the last $ARGUMENTS days (default: LC_COLLECT_DAYS from .env, or 7).

## Overview

One command, five phases. The agent handles everything automatically, only pausing for Phase 5 (review) if there are flagged items.

```
Phase 1: Collect from sources (parallel MCP calls)
Phase 2: Process + resolve (deterministic scripts)
Phase 3: Enrich via LinkedIn (if MCP available)
Phase 4: Report (structured summary)
Phase 5: Review (only if flagged items)
```

## Phase 1: Collect

### Pre-flight

```bash
source .env 2>/dev/null
```

1. Check `./data/contacts.db` exists. If not: `sqlite3 ./data/contacts.db < schema.sql`
2. Determine time window: use `$ARGUMENTS` if provided, else `LC_COLLECT_DAYS` from `.env`, else 7.
3. Create run record:
   ```bash
   sqlite3 data/contacts.db "INSERT INTO runs (started_at, source) VALUES (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'all'); SELECT last_insert_rowid();"
   ```
4. Check which MCP servers are available (try a lightweight call on each). Report:
   ```
   Sources available: Gmail, Calendar, Slack
   ```
   Or: `Sources available: Gmail, Slack (Calendar: not configured)`

### Collect from all sources (parallel)

Call all available MCP sources **in parallel** (single message with parallel tool calls). Save each response to a temp file.

**Gmail** (google-workspace MCP):
1. `search_gmail_messages(query="newer_than:Nd -label:promotions -label:social -label:updates", user_google_email=..., page_size=25)` -- paginate. The label filters skip low-value categories at the API level.
2. `get_gmail_messages_content_batch(message_ids=[...], format="metadata")` -- max 25 per batch. MUST use `format="metadata"` (headers only, no body) to minimize tokens.
3. Save to `/tmp/lc_gmail.txt`. Do NOT read or summarize the content -- save the raw MCP response directly.

**Calendar** (google-workspace MCP):
1. `get_events(user_google_email=..., time_min=..., time_max=..., max_results=50, detailed=true)`
2. Save to `/tmp/lc_calendar.txt`

**Slack** (slack MCP) -- DM-first approach:
1. `channels_list(channel_types="im,mpim")` -- get all DM and group DM channel IDs
2. For each channel that has recent activity, call `conversations_history(channel_id=..., limit="7d")`
3. Save output to `/tmp/lc_slack.txt` with channel headers:
   ```
   ===CHANNEL D12345 (im)===
   <conversations_history output>
   ===CHANNEL G67890 (mpim)===
   <conversations_history output>
   ```
4. (Optional) `conversations_search_messages(filter_users_with="@me", filter_date_after=...)` for thread interactions in public channels. Append to the same file.

**IMPORTANT: Save the FULL raw MCP response to each temp file. Do NOT pre-filter, summarize, or trim the output. `parse-source.py` handles all filtering deterministically.**

### Slack user lookups (cache misses only)

Check for cache misses:
```bash
python3 scripts/parse-source.py --source slack --run-id <RUN_ID> < /tmp/lc_slack.txt 2>&1 >/dev/null | grep "Cache misses"
```
If misses: call `users_search` for each missed UID, append to `/tmp/lc_slack.txt` after `---` separator.

Report:
```
Collecting from Gmail, Calendar, Slack (last N days)...
  Gmail:    X messages -> Y sightings
  Calendar: X events -> Y sightings
  Slack:    X messages -> Y sightings (Z cache misses resolved)
```

## Phase 2: Process + Resolve

```bash
./scripts/process-run.sh <RUN_ID> /tmp/lc_gmail.txt /tmp/lc_calendar.txt /tmp/lc_slack.txt
```

This runs deterministically:
- Parse all sources (filtering: self, bots, mailing lists, calendar invites, LC_MAX_PARTICIPANTS, sighting dedup)
- Resolve sightings (B1-B5 cascade)
- Auto-connect from `linkedin_connections` CSV
- Update people (scores, sources, names)
- Finalize run

Report the output from `process-run.sh`.

## Phase 3: Enrich

Check if LinkedIn MCP is available. If not:
```
LinkedIn enrichment: linkedin MCP not configured.
  To enable: uvx linkedin-scraper-mcp --login --no-headless
  CSV matching still applied.
```

If available, enrich top new contacts (batch size from `LC_ENRICH_BATCH_SIZE`, default 10):

```sql
SELECT id, name, company, company_domain, email FROM people
WHERE linkedin_url IS NULL AND status NOT IN ('connected', 'ignored') AND name LIKE '% %'
ORDER BY interaction_score DESC LIMIT <batch_size>;
```

For each candidate:
1. Call `search_people(keywords="{name} {company}")` via LinkedIn MCP
2. Parse response: check URL, headline, degree, mutual connections
3. If confident match: UPDATE `people` with `linkedin_url`, INSERT into `linkedin_searches`
4. If 1st degree + company matches: also set `status = 'connected'`
5. Wait 60s between calls. Max `LC_ENRICH_BATCH_SIZE` per run.

Report:
```
Enriching top N new contacts via LinkedIn...
  Searched: X, Found: Y, Connected: Z
```

## Phase 4: Report

Generate structured report. Exclude contacts with `status = 'ignored'`.

```
=== Weekly Collect Report (last N days) ===

New contacts:    X
Score changes:   Y (significant increases)
Total:           Z (W ignored, hidden)
With LinkedIn:   A
Connected:       B

Top new contacts:
 1. [score] Name    email    sources    status
 ...

Score movers (biggest increases):
 1. [score +delta] Name -- reason
 ...

Flagged for review: X
```

For empty weeks: `No new contacts this week. Z people in database.`

## Phase 5: Review (only if flagged)

Check for flagged items. Present each to the user.

### B4 fuzzy candidates
If `process-run.sh` output shows unresolved sightings with fuzzy matches, present them. For each: match to existing person (INSERT `name_domain` rule) or skip.

### Duplicates
```sql
SELECT a.id, a.name, a.email, b.id, b.name, b.email
FROM people a JOIN people b ON a.id < b.id
WHERE LOWER(a.name) = LOWER(b.name) AND a.company_domain = b.company_domain
  AND a.status != 'ignored' AND b.status != 'ignored';
```
To merge: `./scripts/merge-people.sh --keep <id> --merge <id> --reason "..."`

### Incomplete names (score >= 5)
```sql
SELECT id, name, email, interaction_score FROM people
WHERE name NOT LIKE '% %' AND interaction_score >= 5 AND status != 'ignored'
ORDER BY interaction_score DESC LIMIT 10;
```
Resolve via Slack `users_search` or Google Contacts `search_directory`.

If no flagged items, the run is complete.

## Important

- All filtering is handled by `parse-source.py` -- do not filter manually
- All resolution is handled by `resolve-sightings.sql` -- do not write resolution SQL
- All scoring is handled by `update-people.sql` -- do not recalculate manually
- Agent judgment is only needed for B4 fuzzy matching, merge decisions, and name resolution
- Never store email body content or Slack message text -- metadata only
- If a source MCP fails, continue with others and note in the report
