# Groundwork

Collects contacts from Gmail, Calendar, and Slack via MCP servers, deduplicates them, finds LinkedIn profiles via web search, and stores everything in a local SQLite database.

## Quick start

Say **"install"** to your agent (works in Cursor, Claude Code, or any agent that reads this file). The agent walks you through everything interactively.

```
# In Cursor or Claude Code, just say:
install
```

Three steps to your first collection: install deps, set your email, Google OAuth. That's it — Slack and LinkedIn can be layered in after.

After setup, just say **`start`** to collect, enrich LinkedIn profiles, and open the viewer in one command.

### Manual setup (fallback)

If you prefer to run steps yourself:

```bash
pip install -e ".[direct]"        # Install dependencies
cp .env.example .env              # Set LC_SELF_EMAIL
python3 scripts/setup-auth.py google   # Google OAuth (required)
./scripts/setup.sh                # Init database
./scripts/run-collect.sh          # First collection
```

Optional value-adds (add any time after):

```bash
python3 scripts/setup-auth.py slack      # Slack DMs + mentions
python3 scripts/setup-auth.py linkedin   # LinkedIn profile lookup
python3 scripts/setup-auth.py --check    # Check credential status
```

## Collect flow (any MCP-capable agent)

Say **"start"** (or **"collect"**) to run the full pipeline. Scripts handle all plumbing; the agent only intervenes for LinkedIn evaluation and flagged items.

### `/start` — the everyday command (collect + enrich + viewer)

```
start [days]
```

Runs the full pipeline: collect → LinkedIn enrich → launch viewer at http://localhost:8080/viewer/index.html. Use this after onboarding and any time you want to refresh your contacts.

### Phase 1-4: Collect + Process + Report (one command)

```bash
./scripts/run-collect.sh [days]
```

This single script handles everything: preflight, run record, source collection (Gmail/Calendar/Slack via MCP), name enrichment from Google Contacts directory, sighting resolution (B1-B5), auto-merge obvious duplicates, and formatted report. Zero agent MCP calls.

Internally chains: `preflight.sh` → `collect-sources.py` → `process-run.sh` → `auto-merge.sh` → report.

### Phase 3b: LinkedIn Enrichment (optional)

```bash
python3 scripts/enrich-linkedin.py [--batch-size 10]
```

The script searches LinkedIn via MCP and saves raw responses to `data/tmp/linkedin/`. The agent then reviews each file, evaluates match quality (name, company, degree), and updates the DB. See `.cursor/skills/linkedin-enrich/SKILL.md` for evaluation rules.

### Phase 5: Review (only if flagged)

If `run-collect.sh` output shows `Flagged for review > 0`: present B4 fuzzy candidates and ambiguous duplicates. Obvious duplicates are auto-merged. Merge with `./scripts/merge-people.sh`.

### Status

```bash
./scripts/status.sh
```

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
- `scripts/` -- Pipeline scripts (collect-sources, parse-source, process-run, setup, etc.)
- `scripts/server.py` -- Dev server for the viewer with auto-save endpoint (`POST /api/save`)
- `viewer/` -- Single-file HTML viewer (auto-saves via server.py, no manual save button)
- `data/` -- SQLite database (gitignored)
- `docs/research/` -- Background research

## Database

- Location: `./data/contacts.db`
- Access via: `sqlite3 ./data/contacts.db`
- Nine tables:
  - `people` -- canonical deduplicated person (the product)
  - `sightings` -- raw contact appearances from sources (replaces interactions)
  - `matching_rules` -- explicit identity resolution rules (email, slack_uid, name_domain)
  - `merge_log` -- audit trail for person merges (snapshot + reason)
  - `linkedin_searches` -- enrichment traceability (queries, candidates, choices)
  - `slack_users` -- cache of Slack user lookups (avoids redundant MCP calls)
  - `runs` -- collection/enrichment run bookkeeping
  - `linkedin_connections` -- imported LinkedIn 1st-degree connections (CSV)
  - `contact_names` -- cached email-to-name lookups from Google Contacts directory

## Rules for commands

### Deduplication
- Identity resolution is driven by the `matching_rules` table -- the agent writes explicit rules, and they are applied automatically in future runs
- The agent only uses judgment for new, ambiguous cases (Phase B4 in the Resolution Protocol below), which also creates a durable rule
- When merging, keep the most complete version of each field (longest name, earliest first_seen, latest last_seen)
- Recalculate interaction_score from the `sightings` table after each merge
- All merges are logged in `merge_log` with a JSON snapshot of the deleted person (reversible)
- See the **Resolution Protocol** section below for the full matching sequence

### Scoring
Calculated from the `sightings` table (not incrementally). Interactions are split into
**strong signal** (uncapped, full weight) and **weak signal** (unified cap of 3 pts total).

```
interaction_score = ROUND(strong_direct_score × div_multiplier)
                  + weak_signal_points          -- 1 pt per 3 weak events
                  + has_direct_bonus            -- +5 if any strong interaction exists
```

**Strong signal interactions** -- size-aware weights per sighting:
- 1:1 meeting (1 other attendee in sightings): 5 points each
- Small group meeting (2-4 others): 4 points each
- Slack DMs (date-bucketed, 1 sighting per active day): 4 points each
- 1:1 email_sent (only 1 other person on the message): 3 points each
- Multi-recipient email_sent: 2 points each
- 1:1 email_received (per-thread dedup, 1 other person on thread): 2 points each
- Multi-recipient email_received (per-thread dedup): 1 point each

**Weak signal pool** -- linear at 1 pt per 3 distinct weak events (no hard cap):
- Medium group meetings (5+ others, is_group=0): counts toward pool
- Large meetings (is_group=1): counts toward pool
- Mailing list / group emails (is_group=1): counts toward pool
- `weak_signal_points = total_weak_events / 3` (3 events = 1 pt, 28 events = 9 pts)

**Has-direct bonus**: +5 pts if `strong_direct_score > 0`. Guarantees that anyone you've
had at least one real interaction with scores at least 6 pts — always above the 3 pt
weak-signal cap. Uncrossable gap between "you engaged directly" and "only appeared in blasts."

**Diversity multiplier** (applied to `strong_direct_score` only):
- channel_diversity = 1 → 1.0×
- channel_diversity = 2 → 1.5×
- channel_diversity = 3 → 2.5×
- channel_diversity = 4+ → 4.0×

`channel_diversity` counts distinct strong-signal interaction types (meeting, slack_dm,
email_sent, email_received) — medium-group meetings excluded since they go to the weak pool.

**Score tier guarantees:**
- Weak-signal only: 0–3 pts
- Has any direct contact: 6+ pts (minimum: 1 multi-recipient email + bonus = 1+5)
- Has 1:1 or DM: 9–10+ pts (4 pts DM + 5 bonus, or 5 pts 1:1 + 5 bonus)
- Multi-channel ongoing relationship: 50–300+ pts

### LinkedIn confidence
- **high**: Name AND company both match the LinkedIn profile clearly
- **medium**: Name matches but company is different, missing, or ambiguous
- **low**: Multiple candidates, very generic name, or weak match
- **null**: Not yet searched

### Contact status
- **new** -- just collected, no review yet
- **reviewed** -- agent or user has reviewed, not yet connected on LinkedIn
- **connected** -- confirmed 1st-degree LinkedIn connection (via CSV import or live MCP check)
- **wrong_match** -- user reviewed on LinkedIn and confirmed the match was wrong
- **ignored** -- intentionally skipped

### LinkedIn connections table
- `linkedin_connections` stores your LinkedIn 1st-degree connections, imported from LinkedIn's CSV export
- Always check this table before doing web searches during `/enrich` -- matching by email or name is free and instant
- Run `./scripts/import-connections.sh` to refresh after exporting new data from LinkedIn

### Sighting logging
- Every contact discovered in a run gets a `sightings` row, even if the person already exists
- Raw identity fields (`raw_name`, `raw_email`, `source_uid`, `raw_username`, `raw_company`, `raw_title`) are **immutable** -- they capture exactly what was seen in the source, never overwritten
- `is_group` flag: 1 for group/broadcast interactions (mailing list emails, meetings >10 attendees, slack channels), 0 for direct interactions. Set at parse time by `parse-source.py`.
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

**All filtering is handled in two layers: `collect-sources.py` applies Gmail query-level filters (label exclusions, noreply senders) at the API level, then `parse-source.py` applies detailed filtering (skip patterns, participant limits) during parsing.**

Contacts skipped by the parser:
- Your own email (`LC_SELF_EMAIL` from `.env`)
- Non-person addresses: `noreply@`, `comments-noreply@`, `notifications@`, `no-reply@`, `support@`
- Distribution lists: `*-list@`, `*-all@`, `*-team@`, `*-sme@`, `*-eng@`, `*-announce@`, `*-managers@`, `*-directs@`, `*-leadership@`, `*-specialists@`, `*-devel@`, `*-program@`, `*-updates@`, `*-docs@`, `*-qe@`, `*-bu@`, `*-marketing@`, `*-tooling@`, `*-notes@`
- Multi-segment list addresses: emails with 2+ hyphens in the local part (e.g., `ai-bu-cai@`, `openshift-ai-eng-senior-leadership@`)
- Calendar invitations in Gmail (subjects starting with `Invitation:`, `Accepted:`, `Declined:`, `Updated:`)
- Jira (`jira-issues@*`, `*@*.atlassian.net`), Slack (`notification@slack.com`), newsletters (`fridayfive@*`, `announce-list@*`)
- Calendar resources (`@resource.calendar.google.com`, `@group.calendar.google.com`)
- Google Groups (`@googlegroups.com`)

**Skip entire emails/meetings when participant count exceeds `LC_MAX_PARTICIPANTS`:**
- Count all To + Cc recipients on an email, or all attendees on a calendar event
- If the count exceeds the threshold (default 80), skip the entire message/event -- don't create sightings for any participant
- This filters out all-hands meetings, large mailing list threads, and org-wide announcements
- The threshold is configurable via `.env` (`LC_MAX_PARTICIPANTS=80`). Set to `0` to disable.

**Group interaction detection** (handled by `parse-source.py`):

Principle: if you're there via group membership, it's a group sighting. If you're individually named/addressed, it's direct -- regardless of how many people are in the conversation.

- **Gmail**: Uses RFC list headers from the MCP (`List-Unsubscribe`, `Precedence: list`, `List-Id`). Present = `is_group = 1`. Absent = `is_group = 0`. A 30-person CC thread is direct if it has no list headers.
- **Calendar**: Weighted attendee count. Individual emails count as 1, group/list addresses (caught by `should_skip_email`) count as 5. If weighted total > `GROUP_MEETING_THRESHOLD` (20), tagged `is_group = 1`. A 19-person meeting with all individual invitees stays direct.
- **Slack**: `slack_channel` = `is_group = 1`, `slack_dm` = `is_group = 0`.
- Group sightings contribute 1 point per unique event/thread, capped at 3 total per person (vs full weight for direct)

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
5. **`interaction_score`**: recalculate from ALL sightings (direct=full weight, group=1 per thread capped at 3)
6. **`channel_diversity`**: count of distinct direct interaction types
7. **`updated_at`**: set to now

Batch SQL for Phase D is in `scripts/update-people.sql` (run after all sightings in a run are linked).

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

Four MCP servers are available. The first three run as Docker containers (via `docker-compose.yml` in `local-automation-mcp/`) behind an mcp-proxy gateway (`localhost:9090`):

- **google-workspace** -- Gmail search/read, Calendar events/attendees (port 8000)
- **google-contacts** -- Google Contacts + Workspace directory lookup (port 8082)
- **slack** -- Slack channels, DMs, user profiles (port 13070, uses xoxc/xoxd browser tokens)
- **linkedin** -- LinkedIn profile lookup, connection status (via `linkedin-scraper-mcp`, runs locally via `uvx`)

The gateway servers are exposed via SSE at `http://localhost:9090/<server>/sse` and connected using `mcp-remote`. The LinkedIn MCP runs as a local stdio process.

Credentials are managed by `setup-auth.sh` which extracts tokens from Chrome and writes them to `local-automation-mcp/mcp-secrets.env`. Run `./scripts/setup-auth.sh --check` to verify token freshness.

Config lives in `.mcp.json` at project root (Claude Code) and `.cursor/mcp.json` (Cursor). Both are gitignored.

### Google MCP auth

Assume the user is already authenticated. Try MCP operations directly -- do not preemptively call `start_google_auth`. Only initiate authentication when you receive an explicit auth error (e.g., "Authentication required", "Invalid credentials", "OAuth token expired").

### Slack MCP handling

`collect-sources.py` handles Slack collection automatically using `conversations_search_messages(filter_users_with="@me")` -- a single call that returns all DMs and thread interactions involving you (replaces the previous 70+ per-channel approach).

For manual Slack lookups (Phase 5 review, name resolution), the agent can still call:
- `users_search(query="<name or user_id>")` -- returns email, title, DM channel ID
- `conversations_history(channel_id=..., limit="7d")` -- for specific channel history

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

## Learned User Preferences

- Auto-save all DB changes immediately -- no manual "Save" buttons in the viewer
- Prefer inline/side-panel interactions over opening new browser tabs
- User wants to be able to review errors and wrong matches later (filterable states)
- When adding data protections, be thorough: validate payloads, backup before writes, guard against row count drops
- Never test write endpoints with garbage data against production files -- use a separate test path
- Group interactions (mailing lists, large meetings, Slack channels) are weaker signals than direct ones (DMs, sent emails, small meetings) -- scoring should reflect this
- Channel diversity (distinct interaction types per person) is a better review-priority signal than raw interaction score alone
- Prefer `uv` for Python dependency management and virtual environments over plain pip
- Prefer automated credential extraction (pycookiecheat, browser APIs) over manual copy-paste from dev tools
- Viewer should auto-load `contacts.db` when it exists, with controls to load another DB or return to the main screen

## Learned Workspace Facts

- System Python is 3.9 -- use `from __future__ import annotations` for modern type hints (e.g., `X | None`)
- Viewer dev server is `python3 scripts/server.py` (default port 8080), not plain `python3 -m http.server`
- `data/backups/` holds rotating DB backups created by server.py (startup + pre-save, last 20 kept)
- Server validates saves: SQLite header, minimum 4KB size, `people` table exists, rejects >50% row count drops
- DB backup exists at `data/contacts.db.bak` (manually created, from March 6)
- LinkedIn blocks iframes (`X-Frame-Options: DENY`) -- viewer uses `window.open` with a named reusable popup instead
- LinkedIn data export page no longer has a separate "Connections" checkbox -- must request the full data archive to get `Connections.csv`. Archive arrives ~15 min after request as `Basic_LinkedInDataExport_MM-DD-YYYY.zip.zip` in Downloads.
- `sightings.is_group` flag distinguishes group/broadcast interactions from direct ones; `people.channel_diversity` tracks interaction type breadth
- Orphan `people` records (no sightings/matching_rules) from run 2 should be deleted -- resolution logic now prevents new orphans
- MCP Docker stack (Google, Slack, Atlassian, mcp-proxy) lives in sibling repo `local-automation-mcp/` -- only needed when using `--provider mcp`; direct provider bypasses it entirely
- Direct provider (`--provider direct`, the default) extracts Slack `xoxd`/`xoxc` and LinkedIn `li_at` cookies via `pycookiecheat` from Chrome's on-disk cookie DB -- no Docker, no sibling repo required
- `scripts/providers/` package provides `direct_provider.py` (Google OAuth + Slack Web API), `mcp_provider.py` (legacy MCP), and `linkedin_direct.py` (LinkedIn Voyager API via `li_at` cookie)
- `scripts/setup-auth.py` is the self-contained auth wizard (Google OAuth flow + Slack/LinkedIn Chrome cookie extraction); replaces `setup-auth.sh` and the `local-automation-mcp/` dependency
- `pyproject.toml` has dep groups `[google]`, `[slack]`, `[linkedin]`, `[mcp]`, `[direct]`, `[all]`; new users run `pip install -e ".[direct]"` then `python3 scripts/setup-auth.py`
- `data/.credentials/` stores Google OAuth token (`google.json`), Slack tokens (`slack.json`), and LinkedIn cookie (`linkedin.json`) -- all gitignored
