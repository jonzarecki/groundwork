# Linked Collector

Collects contacts from Gmail, Calendar, and Slack via MCP servers, deduplicates them, finds LinkedIn profiles via web search, and stores everything in a local SQLite database.

## Quick start

```bash
cp .env.example .env          # Set LC_SELF_EMAIL to your email
./scripts/setup.sh            # Init database + configure MCP servers
# Then say "collect" or "run" to the agent
```

## Collect flow (any MCP-capable agent)

Say **"collect"** or **"run"**. The agent handles everything in 5 phases. This section is the single source of truth -- platform-specific commands (`.claude/commands/collect.md`, `.cursor/rules/collect.mdc`) are thin wrappers that reference this.

### Phase 1: Collect from sources

1. `source .env 2>/dev/null`
2. Create run: `sqlite3 data/contacts.db "INSERT INTO runs (started_at, source) VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'all'); SELECT last_insert_rowid();"`
3. Call MCP sources **in parallel** (use parallel tool calls where supported). Save responses to temp files:
   - **Gmail**: `search_gmail_messages(query="newer_than:Nd -label:promotions -label:social -label:updates", user_google_email=..., page_size=25)` -> paginate -> `get_gmail_messages_content_batch(message_ids=[...], format="metadata")` -> save raw to `/tmp/lc_gmail.txt`
   - **Calendar**: `get_events(user_google_email=..., time_min=..., time_max=..., max_results=50, detailed=true)` -> save to `/tmp/lc_calendar.txt`
   - **Slack**: `channels_list(channel_types="im,mpim")` -> `conversations_history(channel_id=..., limit="7d")` per channel -> save to `/tmp/lc_slack.txt` with `===CHANNEL <id> (<type>)===` headers
4. Check Slack cache misses: `python3 scripts/parse-source.py --source slack --run-id <ID> < /tmp/lc_slack.txt 2>&1 >/dev/null | grep "Cache misses"`. Call `users_search` for misses, append after `---` separator.
5. Skip sources that are not configured or fail (note in report, continue with others).

### Phase 2: Process (one command, deterministic)

```bash
./scripts/process-run.sh <RUN_ID> /tmp/lc_gmail.txt /tmp/lc_calendar.txt /tmp/lc_slack.txt
```

This parses all sources, resolves identities (B1-B5), auto-connects from LinkedIn CSV, updates scores/names, finalizes the run. All deterministic -- no agent judgment needed.

### Phase 3: Enrich (optional, needs LinkedIn MCP)

If `linkedin` MCP is not available: report it, suggest `uvx linkedin-scraper-mcp --login --no-headless`, continue.

If available: query top `LC_ENRICH_BATCH_SIZE` (default 10) unenriched contacts with full names ordered by score. For each, call `search_people(keywords="{name} {company}")`, parse the response, update the DB. Wait 60s between calls. See `.cursor/skills/linkedin-enrich/SKILL.md` for the full strategy.

### Phase 4: Report

Format the structured output from `process-run.sh` into a report showing: new contacts, score movers, LinkedIn stats, flagged items. Exclude contacts with `status = 'ignored'`.

### Phase 5: Review (only if flagged)

If `process-run.sh` output shows `FLAGGED_TOTAL > 0`: present B4 fuzzy candidates, duplicate pairs, incomplete names. Merge with `./scripts/merge-people.sh`. Most runs have zero flags.

## Configuration

Settings live in `.env` (gitignored). Copy `.env.example` to `.env` to customize.

| Variable | Default | Description |
|----------|---------|-------------|
| `LC_SELF_EMAIL` | (required) | Your email address -- filtered from sightings |
| `LC_MAX_PARTICIPANTS` | `80` | Skip emails/meetings with more participants than this |
| `LC_COLLECT_DAYS` | `7` | Default collection window in days |
| `LC_ENRICH_BATCH_SIZE` | `10` | Max contacts to enrich per collect run |

## Project structure

- `SPEC.md` -- Product spec (use cases, data model, scoring)
- `ROADMAP.md` -- Phased milestones
- `.claude/commands/` -- Claude Code slash commands (collect, enrich, status)
- `.cursor/rules/collect.mdc` -- Cursor agent collection playbook
- `.cursor/skills/linkedin-enrich/` -- LinkedIn enrichment strategy (3-tier search with traceability)
- `schema.sql` -- SQLite schema
- `scripts/` -- Shell scripts (setup, init-db, export-csv, import-connections)
- `viewer/` -- Single-file HTML viewer
- `data/` -- SQLite database (gitignored)
- `docs/research/` -- Background research

## Database

- Location: `./data/contacts.db`
- Access via: `sqlite3 ./data/contacts.db`
- Eight tables:
  - `people` -- canonical deduplicated person (the product)
  - `sightings` -- raw contact appearances from sources (replaces interactions)
  - `matching_rules` -- explicit identity resolution rules (email, slack_uid, name_domain)
  - `merge_log` -- audit trail for person merges (snapshot + reason)
  - `linkedin_searches` -- enrichment traceability (queries, candidates, choices)
  - `slack_users` -- cache of Slack user lookups (avoids redundant MCP calls)
  - `runs` -- collection/enrichment run bookkeeping
  - `linkedin_connections` -- imported LinkedIn 1st-degree connections (CSV)

## Rules for commands

### Deduplication
- Identity resolution is driven by the `matching_rules` table -- the agent writes explicit rules, and they are applied automatically in future runs
- The agent only uses judgment for new, ambiguous cases (Phase B4 in the Resolution Protocol below), which also creates a durable rule
- When merging, keep the most complete version of each field (longest name, earliest first_seen, latest last_seen)
- Recalculate interaction_score from the `sightings` table after each merge
- All merges are logged in `merge_log` with a JSON snapshot of the deleted person (reversible)
- See the **Resolution Protocol** section below for the full matching sequence

### Scoring
Calculated from the `sightings` table (not incrementally):
- Meetings: 3 points each
- Emails sent: 2 points each
- Emails received: 1 point each
- Slack DMs: 2 points each
- Slack channel mentions: 1 point each

### LinkedIn confidence
- **high**: Name AND company both match the LinkedIn profile clearly
- **medium**: Name matches but company is different, missing, or ambiguous
- **low**: Multiple candidates, very generic name, or weak match
- **null**: Not yet searched

### Contact status
- **new** -- just collected, no review yet
- **reviewed** -- agent or user has reviewed, not yet connected on LinkedIn
- **connected** -- confirmed 1st-degree LinkedIn connection (via CSV import or live MCP check)
- **ignored** -- intentionally skipped

### LinkedIn connections table
- `linkedin_connections` stores your LinkedIn 1st-degree connections, imported from LinkedIn's CSV export
- Always check this table before doing web searches during `/enrich` -- matching by email or name is free and instant
- Run `./scripts/import-connections.sh` to refresh after exporting new data from LinkedIn

### Sighting logging
- Every contact discovered in a run gets a `sightings` row, even if the person already exists
- Raw identity fields (`raw_name`, `raw_email`, `source_uid`, `raw_username`, `raw_company`, `raw_title`) are **immutable** -- they capture exactly what was seen in the source, never overwritten
- Context should be brief: email subject, meeting title, Slack channel name
- Never store email body content or message text -- metadata only
- `source_uid` is the source-native unique identifier: email for Gmail/Calendar, Slack User ID for Slack
- `raw_username` captures human-readable handles (Slack username) for display/debugging

## Resolution Protocol

This is the single source of truth for identity resolution. Both `collect.md` and `collect.mdc` reference this section.

### Source identifier mapping

| Source   | `source_uid`                       | `raw_email`                                   | `raw_username`               |
|----------|------------------------------------|-----------------------------------------------|------------------------------|
| Gmail    | email address (same as raw_email)  | From/To/Cc header                             | NULL                         |
| Calendar | email address (same as raw_email)  | Attendee email                                | NULL                         |
| Slack    | Slack User ID (e.g., U04QAHM6BEP) | From `users_search` (may be NULL or personal) | Slack handle (e.g., jsmith)  |

### Filtering rules

**All filtering is handled by `parse-source.py` -- the agent must save FULL raw MCP responses to temp files without pre-filtering, summarizing, or trimming.**

Contacts skipped by the parser:
- Your own email (`LC_SELF_EMAIL` from `.env`)
- Non-person addresses: `noreply@`, `comments-noreply@`, `notifications@`, `no-reply@`, `support@`
- Distribution lists: `*-list@`, `*-all@`, `*-team@`, `*-sme@`, `*-eng@`, `*-announce@`
- Calendar invitations in Gmail (subjects starting with `Invitation:`, `Accepted:`, `Declined:`, `Updated:`)
- Jira (`jira-issues@*`), Slack (`notification@slack.com`), newsletters (`fridayfive@*`, `announce-list@*`)
- Calendar resources (`@resource.calendar.google.com`, `@group.calendar.google.com`)
- Google Groups (`@googlegroups.com`)

**Skip entire emails/meetings when participant count exceeds `LC_MAX_PARTICIPANTS`:**
- Count all To + Cc recipients on an email, or all attendees on a calendar event
- If the count exceeds the threshold (default 80), skip the entire message/event -- don't create sightings for any participant
- This filters out all-hands meetings, large mailing list threads, and org-wide announcements
- The threshold is configurable via `.env` (`LC_MAX_PARTICIPANTS=80`). Set to `0` to disable.

**Mailing list weighting:**
- When a contact appears in a mailing list thread (detected by Cc containing `*-list@*` or To containing a known list address), score it as `email_received` (1 point) regardless of direction

### Phase A: Insert sighting (always, unconditionally)

```sql
INSERT INTO sightings (run_id, source, source_ref, source_uid,
    raw_name, raw_email, raw_company, raw_title, raw_username,
    interaction_type, interaction_at, context)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
```

The sighting captures exactly what was seen. `raw_*`, `source_uid`, and `raw_username` fields are immutable.

### Phase B: Resolve to a person

Cascade through these checks, stopping at the first match. The system is resilient -- it works whether matching_rules is fully populated or empty.

**B1. Email rule** (skip if raw_email is NULL):
```sql
SELECT person_id FROM matching_rules WHERE identifier_type = 'email' AND identifier_value = ?;
```
match_method = `'exact_email'`, confidence = `'high'`

**B1b. Email fallback on people.email** (if B1 found nothing -- handles missing rules):
```sql
SELECT id FROM people WHERE email = ?;
```
match_method = `'exact_email'`, confidence = `'high'`
If matched, auto-create the missing email rule so B1 catches it next time.

**B2. Source UID rule** (critical for Slack):
```sql
SELECT person_id FROM matching_rules WHERE identifier_type = 'slack_uid' AND identifier_value = ?;
```
match_method = `'exact_source_uid'`, confidence = `'high'`

**B3. Name+domain rule** (previously-judged fuzzy match):
```sql
SELECT person_id FROM matching_rules WHERE identifier_type = 'name_domain' AND identifier_value = ?;
```
(identifier_value = `"raw_name||company_domain"`, e.g., `"J. Smith||corp.com"`)
match_method = `'fuzzy_name'`, confidence from the rule

**B4. Fuzzy name + same domain (no existing rule)** -- agent judgment, creates a NEW rule:
```sql
SELECT id, name, email FROM people WHERE company_domain = ? AND name LIKE '%' || ? || '%';
```
match_method = `'agent_judgment'`, confidence = `'medium'` or `'low'`
If the agent matches, also INSERT a `name_domain` rule with reasoning in `notes`.

**B5. No match** -- create new person + initial rules:
```sql
INSERT INTO people (name, email, company, company_domain, first_seen, last_seen, sources, status)
VALUES (?, ?, ?, ?, ?, ?, ?, 'new');

INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, created_by_run_id, confidence)
VALUES (?, 'email', ?, ?, ?, 'high');          -- if raw_email is not NULL

INSERT INTO matching_rules (person_id, identifier_type, identifier_value, source, created_by_run_id, confidence)
VALUES (?, 'slack_uid', ?, 'slack', ?, 'high'); -- if source is slack
```
For Slack users with no email: `people.email` starts NULL, only a `slack_uid` rule is created. When a future sighting with email matches this person via the slack_uid rule, an `email` rule is added and `people.email` is backfilled.

### Phase C: Link sighting to person

```sql
UPDATE sightings SET
    person_id = ?,
    match_method = ?,
    match_confidence = ?,
    matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = ?;
```

### Phase D: Update person record

After linking a sighting, update the person with all of these:

1. **`last_seen`**: set to `MAX(last_seen, sighting.interaction_at)`
2. **`name`**: if the sighting `raw_name` has a space (first+last) and the current name doesn't, update it
3. **`company` / `company_domain`**: fill in if previously empty
4. **`sources`**: append the source if not already in the comma-separated list
5. **`interaction_score`**: recalculate from ALL sightings for this person
6. **`updated_at`**: set to now

Batch SQL for Phase D (run after all sightings in a run are linked):

```sql
UPDATE people SET
  last_seen = COALESCE((SELECT MAX(interaction_at) FROM sightings WHERE person_id = people.id), last_seen),
  name = COALESCE((SELECT s.raw_name FROM sightings s WHERE s.person_id = people.id AND s.raw_name LIKE '% %' ORDER BY s.interaction_at DESC LIMIT 1), name),
  sources = (SELECT GROUP_CONCAT(DISTINCT source) FROM sightings WHERE person_id = people.id),
  interaction_score = (SELECT COALESCE(SUM(CASE interaction_type
    WHEN 'meeting' THEN 3
    WHEN 'email_sent' THEN 2
    WHEN 'email_received' THEN 1
    WHEN 'slack_dm' THEN 2
    WHEN 'slack_channel' THEN 1
  END), 0) FROM sightings WHERE person_id = people.id),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id IN (SELECT DISTINCT person_id FROM sightings WHERE run_id = ?);
```

### Merge protocol

When the agent determines two people are the same:

1. **Snapshot** the loser before deletion:
   ```sql
   INSERT INTO merge_log (kept_person_id, merged_person_id, merged_person_snapshot, reason, run_id)
   VALUES (?, ?, json_object('id', p.id, 'name', p.name, 'email', p.email,
       'company', p.company, 'interaction_score', p.interaction_score), ?, ?);
   ```

2. **Reassign sightings**:
   ```sql
   UPDATE sightings SET person_id = <kept> WHERE person_id = <merged>;
   ```

3. **Reassign matching_rules**:
   ```sql
   UPDATE matching_rules SET person_id = <kept> WHERE person_id = <merged>;
   ```

4. **Update** kept person with best fields from both records

5. **Recalculate** interaction_score from sightings

6. **Delete** merged person:
   ```sql
   DELETE FROM people WHERE id = <merged>;
   ```

### LinkedIn search logging

Every search attempt is logged in `linkedin_searches`, even failures:

```sql
INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
VALUES (?, ?, ?, ?, ?, ?, ?);
```

- `candidates`: JSON array of search results `[{url, name, headline, company}, ...]`
- `chosen_url`: the selected URL, or NULL if no match
- `notes`: agent reasoning for the choice or why it was skipped

## How to work here

- Small incremental commits with conventional messages (`feat:`, `fix:`, `refactor:`, etc.)
- Update `.context/progress.md` after completing tasks
- Update `.context/activeContext.md` when switching focus
- Run `/status` to see database stats (contacts, LinkedIn coverage, recent runs)
- Run `/plan` to decide what to work on next (reads TASKS.md, SPEC.md, ARCH.md)
- Run `/review` before committing (code review on staged changes)
- See `SPEC.md` for product requirements, `ARCH.md` for architecture, `TASKS.md` for task list

## MCP servers

Four MCP servers are available. The first three are accessed through the mcp-proxy gateway (`localhost:9090`):

- **google-workspace** -- Gmail search/read, Calendar events/attendees
- **google-contacts** -- Google Contacts + Workspace directory lookup
- **slack** -- Slack channels, DMs, user profiles
- **linkedin** -- LinkedIn profile lookup, connection status (via `linkedin-scraper-mcp`, runs locally via `uvx`)

The gateway servers are exposed via SSE at `http://localhost:9090/<server>/sse` and connected using `mcp-remote`. The LinkedIn MCP runs as a local stdio process.

Config lives in `.mcp.json` at project root (Claude Code) and `.cursor/mcp.json` (Cursor). Both are gitignored.

### Google MCP auth

Assume the user is already authenticated. Try MCP operations directly -- do not preemptively call `start_google_auth`. Only initiate authentication when you receive an explicit auth error (e.g., "Authentication required", "Invalid credentials", "OAuth token expired").

### Slack MCP handling (DM-first approach)

Capture **who you actually talked to**, not channel noise. Two-step process:

**Step 1: DMs and group DMs (high signal)**
1. `channels_list(channel_types="im,mpim")` -- get all DM/MPDM channel IDs (one call)
2. For each channel: `conversations_history(channel_id=..., limit="7d")` -- recent messages
3. Save with headers: `===CHANNEL <id> (im)===` or `===CHANNEL <id> (mpim)===`

**Step 2 (optional): Thread interactions in public channels**
1. `conversations_search_messages(filter_users_with="@me", filter_date_after=...)` -- threads you participated in
2. Append to the same output file

**User lookups:** Use `slack_users` cache table first. Only call `users_search` for cache misses. Cache hit rate is 90%+ after first run.

### MCP failure protocol

When an MCP tool fails for a core operation (Gmail/Calendar/Slack collection):

- **Stop that source immediately** and report the error clearly
- **Continue with other sources** if possible (don't fail the entire run)
- **Record the failure** in the run's `notes` column
- **Never produce partial/misleading data** -- if a source fails mid-collection, discard its partial results

## Non-negotiables

- All data stays in `./data/contacts.db` -- no external storage
- Never auto-send LinkedIn connection requests
- Never store email/message body content
- The `data/` directory is gitignored -- no personal data in the repo
- Do not create new top-level folders without updating ARCH.md
- Schema changes must update both `schema.sql` and `ARCH.md`
