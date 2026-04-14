# Groundwork -- Product Spec

## One-liner

A background job that collects every person you interact with across Gmail, Calendar, and Slack, deduplicates them, finds their LinkedIn profile, and outputs a single ranked list.

## How it works

There is no traditional backend. The entire pipeline is an **AI agent** (Claude Code) with MCP servers for Gmail, Calendar, and Slack. You run a command, the agent does the work, results go into a SQLite database. You look at the database when you feel like it.

```
Inputs:  Gmail, Calendar, Slack (via MCP servers)
Process: Collect (raw sightings) → Resolve (matching_rules) → Enrich (LinkedIn searches)
Output:  SQLite database → HTML viewer / CSV export
```

Every matching decision is stored as an explicit rule in the database. Rules are applied automatically in future runs -- the agent only uses judgment for new, ambiguous cases.

## Use cases

### 1. Post-event catch-up

You go to a conference or event. You talk to people. Their traces are in your calendar (event invites), email (follow-up threads), and Slack (someone added you to a channel or DM'd you). A week later you've forgotten half of them.

Run the collector. It surfaces everyone you interacted with during that period, cross-referenced across channels, with LinkedIn profiles found. You spend 10 minutes connecting while the context is still warm.

### 2. "Who was that?"

Someone mentions a name. You think you've talked to them before but can't remember where. Search the database by name. See where you interacted (Gmail thread, Calendar invite, Slack DM) and when.

### 3. Weekly LinkedIn hygiene

You interact with dozens of people per week across channels. Some are transactional, some are genuinely interesting people you should stay connected with. You never think about it because the friction is too high.

Run the collector weekly. Review the list: "Here are people you interacted with this week that you're not connected with on LinkedIn, ranked by interaction depth." Review, click LinkedIn link, connect. 5 minutes.

### 4. Slack as a contact source

You're in multiple Slack workspaces (company, OSS communities, industry groups). You DM people, have threads, get introduced in channels. These interactions are rich but invisible to any contact management tool.

The collector treats Slack DMs and channel interactions as first-class contact signals, same as email and calendar.

## Data model

### People table (the product)

The main output. One row per deduplicated person.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| name | TEXT | Best-known full name |
| email | TEXT | Primary email (unique) |
| company | TEXT | Company name (inferred from email domain or context) |
| company_domain | TEXT | Email domain (e.g. anthropic.com) |
| linkedin_url | TEXT | Best-match LinkedIn profile URL |
| linkedin_confidence | TEXT | high / medium / low / null |
| interaction_score | INTEGER | Weighted sum of all interactions |
| first_seen | TEXT | ISO timestamp of first interaction |
| last_seen | TEXT | ISO timestamp of most recent interaction |
| sources | TEXT | Comma-separated list: gmail, calendar, slack |
| status | TEXT | new / reviewed / connected / ignored |
| notes | TEXT | Optional free-text notes |
| created_at | TEXT | Row creation timestamp |
| updated_at | TEXT | Last update timestamp |

### Sightings table (raw appearances + evidence)

Every raw contact appearance from a source. Preserves the exact identity as seen (immutable `raw_*` fields) plus the interaction metadata. Replaces the old interactions table. Used for scoring, traceability, and "why this person" explainability.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| run_id | INTEGER | FK → runs.id |
| source | TEXT | gmail / calendar / slack |
| source_ref | TEXT | Source-specific reference (message_id, event_id, thread_ts) |
| source_uid | TEXT | Source-native unique ID (email for Gmail/Cal, Slack User ID for Slack) |
| raw_name | TEXT | Name as extracted from source (immutable) |
| raw_email | TEXT | Email as extracted (immutable, may be NULL for Slack) |
| raw_company | TEXT | Company as inferred from source (immutable) |
| raw_title | TEXT | Job title if available (immutable) |
| raw_username | TEXT | Source handle, e.g. Slack username (immutable) |
| interaction_type | TEXT | email_sent / email_received / meeting / slack_dm / slack_channel |
| interaction_at | TEXT | When the interaction happened |
| context | TEXT | Brief description (subject line, meeting title, channel name) |
| person_id | INTEGER | FK → people.id (NULL until resolved) |
| match_method | TEXT | How this sighting was matched: exact_email / exact_source_uid / fuzzy_name / agent_judgment / manual |
| match_confidence | TEXT | high / medium / low |
| matched_at | TEXT | When the resolution happened |
| created_at | TEXT | Row creation timestamp |

### Matching rules table (identity resolution)

Explicit rules that map identifiers to people. Written by the agent, applied automatically in future runs. Users can manually edit to correct mistakes.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| person_id | INTEGER | FK → people.id |
| identifier_type | TEXT | email / slack_uid / name_domain |
| identifier_value | TEXT | The actual identifier value |
| source | TEXT | Which source triggered this rule |
| created_by_run_id | INTEGER | FK → runs.id |
| confidence | TEXT | high / medium / low |
| notes | TEXT | Agent reasoning (especially for name_domain rules) |
| created_at | TEXT | Row creation timestamp |

Unique constraint on `(identifier_type, identifier_value)` -- one person per identifier.

### Merge log table (dedup audit trail)

Records every person merge. The deleted person is snapshotted as JSON before deletion, making merges reversible.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| kept_person_id | INTEGER | FK → people.id (the surviving person) |
| merged_person_id | INTEGER | The deleted person's former ID |
| merged_person_snapshot | TEXT | JSON snapshot of the deleted person row |
| reason | TEXT | Why the merge happened |
| run_id | INTEGER | FK → runs.id |
| merged_at | TEXT | When the merge happened |

### LinkedIn searches table (enrichment traceability)

Every LinkedIn search attempt, including failures. Preserves the full candidate list and agent reasoning.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| person_id | INTEGER | FK → people.id |
| run_id | INTEGER | FK → runs.id |
| search_query | TEXT | The actual search query string |
| candidates | TEXT | JSON array of search results [{url, name, headline, company}] |
| chosen_url | TEXT | The selected URL, or NULL if no match |
| confidence | TEXT | high / medium / low |
| notes | TEXT | Agent reasoning for the choice |
| searched_at | TEXT | When the search happened |

### Runs table (bookkeeping)

Log of each collection run for debugging and tracking.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| started_at | TEXT | When the run started |
| finished_at | TEXT | When the run finished |
| source | TEXT | gmail / calendar / slack / all / enrich |
| contacts_found | INTEGER | Total contacts encountered |
| contacts_new | INTEGER | New contacts added |
| contacts_updated | INTEGER | Existing contacts updated |
| notes | TEXT | Any issues or observations |

### LinkedIn connections table (imported)

Imported from LinkedIn CSV export. Used to check connection status during enrichment before doing web searches.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| first_name | TEXT | First name from LinkedIn |
| last_name | TEXT | Last name from LinkedIn |
| linkedin_url | TEXT | Profile URL (unique) |
| email | TEXT | Email if available |
| company | TEXT | Company from LinkedIn |
| position | TEXT | Job title from LinkedIn |
| connected_on | TEXT | Date connected |
| created_at | TEXT | Row creation timestamp |

## Scoring

Simple weighted sum, recalculated from the `sightings` table on each collection run:

```
interaction_score =
    (meetings × 3) +
    (emails_sent × 2) +
    (emails_received × 1) +
    (slack_dms × 2) +
    (slack_channel_mentions × 1)
```

No decay for now -- just raw counts. Sort by score descending to see the most-interacted-with people first.

## Deduplication rules

Identity resolution uses the `matching_rules` table. The agent writes explicit rules; they are applied automatically in future runs. See `CLAUDE.md` Resolution Protocol for the full sequence.

### Resolution cascade (Phase B)

Each sighting is resolved by checking these rules in order, stopping at the first match:

1. **B1 -- Email rule:** `matching_rules` lookup by email. Automatic, high confidence.
2. **B2 -- Source UID rule:** `matching_rules` lookup by Slack User ID. Automatic, high confidence. Handles Slack users whose email differs from Gmail/Calendar.
3. **B3 -- Name+domain rule:** `matching_rules` lookup by `"name||domain"`. Automatic, uses confidence from the rule.
4. **B4 -- Agent judgment:** Fuzzy name + same domain, no existing rule. The agent decides and **creates a new `name_domain` rule** with reasoning in `notes`. This is the only step requiring LLM judgment.
5. **B5 -- New person:** No match found. Create person + initial rules (email, slack_uid as applicable).

Once the agent makes a matching decision, it becomes a rule in `matching_rules`. Future runs apply it automatically without re-reasoning.

### Source identifiers

| Source   | Unique ID used for matching     | Email availability |
|----------|---------------------------------|--------------------|
| Gmail    | Email address                   | Always present     |
| Calendar | Email address                   | Always present     |
| Slack    | Slack User ID (e.g., U04QAHM6BEP) | Secondary lookup, may be NULL or personal |

### Merge audit trail

All merges are logged in `merge_log` with a JSON snapshot of the deleted person. Merges reassign both sightings and matching_rules to the surviving person, so all identifiers continue to resolve correctly.

## LinkedIn matching

Three-phase approach:

1. **Check `linkedin_connections` first** -- match by email or name against the imported LinkedIn CSV. Free, instant, always done first.
2. **Web search:** For each remaining person, the agent searches `site:linkedin.com/in "{name}" "{company}"` using whatever web search tool is available.
3. **Confidence scoring:**
   - **high** -- Name and company both match the profile. Unambiguous.
   - **medium** -- Name matches but company is different or missing. Likely correct.
   - **low** -- Multiple candidates, generic name, unclear match.
   - **null** -- No search performed yet or no result found.

Every search attempt is logged in `linkedin_searches` with the query, candidates JSON, chosen URL, and agent reasoning. Even failed or skipped searches are recorded for traceability.

LinkedIn enrichment is a separate command from collection so it can be run independently and less frequently (it consumes web search quota).

## Commands

All commands are Claude Code slash commands (`.claude/commands/`).

### `/collect`

Collects contacts from Gmail, Calendar, and Slack for a configurable time window (default: last 7 days). Deduplicates against existing database. Updates scores.

Usage: `claude /collect` or `claude /collect 30` (last 30 days)

### `/enrich`

Finds LinkedIn profiles for people who don't have one yet. Uses web search.

Usage: `claude /enrich` or `claude /enrich 20` (enrich up to 20 people)

### `/status`

Prints a summary: total contacts, new this week, how many have LinkedIn, top sources, highest-scored unconnected people.

Usage: `claude /status`

## Output

### Primary: SQLite database

Located at `./data/contacts.db`. The source of truth. Queryable with `sqlite3` CLI or any SQLite tool.

### Viewer: HTML page

A single `viewer/index.html` that loads the SQLite database client-side (using sql.js WASM) and renders a sortable, filterable table. No server required -- open the file in a browser.

### Export: CSV

`./scripts/export-csv.sh` dumps the people table to a CSV file for use in spreadsheets or other tools.

## What this is NOT

- Not a CRM. No pipeline stages, no deal tracking, no team features.
- Not a LinkedIn automation tool. No auto-sending invites. Just finding profiles.
- Not a web service. No deployment, no server, no auth. Runs locally.
- Not an "AI insights" platform. No relationship scoring, no intro suggestions, no meeting prep. Just a table.

## Privacy

- All data stays local in the SQLite file.
- The agent accesses Gmail/Calendar/Slack through MCP servers that the user configures themselves.
- No data is sent anywhere except through the MCP servers and web search queries.
- The user controls when the agent runs and what it accesses.
