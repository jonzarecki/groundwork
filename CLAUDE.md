# Linked Collector

Collects contacts from Gmail, Calendar, and Slack via MCP servers, deduplicates them, finds LinkedIn profiles via web search, and stores everything in a local SQLite database.

## Quick start

```bash
./scripts/setup.sh            # Init database + configure MCP servers
# In Cursor: ask the agent to "collect" or "run"
claude /collect               # Or via Claude Code
claude /enrich                # Find LinkedIn profiles
claude /status                # See stats
open viewer/index.html        # Browse results
```

## Configuration

Settings live in `.env` (gitignored). Copy `.env.example` to `.env` to customize. The agent reads `.env` at the start of each run via `source .env` or by reading the file.

| Variable | Default | Description |
|----------|---------|-------------|
| `LC_MAX_PARTICIPANTS` | `80` | Skip emails/meetings with more participants than this. Large all-hands and mailing list blasts add noise. Set to `0` to disable. |

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

**Skip these contacts entirely:**
- Your own email address
- Non-person addresses: `noreply@`, `notifications@`, `no-reply@`, `support@`, `*-list@*`, `*@redhat.com` mailing lists
- Calendar invitations arriving via Gmail (subjects starting with `Invitation:`, `Accepted:`, `Declined:`, `Updated:`, or Message-ID containing `calendar-*@google.com`) -- the Calendar source captures these as meetings
- Jira notifications (`jira-issues@*`), Slack notifications (`notification@slack.com`), newsletters (`fridayfive@*`, `announce-list@*`)

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

Config lives in `.claude/mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor). Both are gitignored.

### Google MCP auth

Assume the user is already authenticated. Try MCP operations directly -- do not preemptively call `start_google_auth`. Only initiate authentication when you receive an explicit auth error (e.g., "Authentication required", "Invalid credentials", "OAuth token expired").

### Slack MCP handling

Thread reading is mandatory -- main channel messages are often just conversation starters.

1. Use `conversations_search_messages` with `filter_date_after` for broad discovery (returns CSV with UserID, UserName, RealName, Channel, ThreadTs)
2. Use `conversations_history(channel_id=..., limit="7d")` for chronological channel messages
3. Scan every message for non-empty `ThreadTs` values
4. Read ALL thread replies via `conversations_replies(channel_id=..., thread_ts=..., limit="7d")`
5. Use parallel tool calls for multiple threads
6. For user info, call `users_search(query="<name or user_id>")` -- returns Email, Title, DMChannelID

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
