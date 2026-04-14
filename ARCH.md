# Architecture

## Overview

Groundwork is a prompt-driven pipeline with no traditional backend code. An AI agent (Claude Code or Cursor) uses MCP servers to access Gmail, Calendar, and Slack, then writes results to a local SQLite database. A static HTML viewer renders the database client-side.

## File Tree

```
groundwork/
├── .claude/
│   ├── commands/
│   │   ├── collect.md         # Main collection command (Gmail + Calendar + Slack)
│   │   ├── enrich.md          # LinkedIn enrichment via web search
│   │   ├── plan.md            # Plan next task
│   │   ├── review.md          # Code review
│   │   └── status.md          # Database stats
│   ├── hooks/
│   │   └── protect-files.sh   # Pre-edit protection for critical files
│   ├── skills/
│   │   ├── commit-changes/SKILL.md
│   │   ├── implement-plan/SKILL.md
│   │   └── review/SKILL.md
│   └── settings.json          # Hook configuration
├── .context/                   # Working memory for AI sessions
├── .cursor/
│   ├── mcp.json               # MCP server config for Cursor (gitignored)
│   ├── rules/
│   │   ├── collect.mdc        # Contact collection playbook for Cursor agent
│   │   └── standards.mdc      # Coding standards
│   └── skills/
│       └── linkedin-enrich/   # LinkedIn enrichment strategy (3-tier search)
├── data/                       # SQLite database (gitignored)
├── docs/
│   └── research/               # Background research and brainstorm transcripts
├── scripts/
│   ├── setup.sh               # Full setup: init DB + configure MCP servers
│   ├── init-db.sh             # Create database from schema
│   ├── export-csv.sh          # Export people table to CSV
│   ├── import-connections.sh  # Import LinkedIn connections CSV
│   ├── parse-source.py        # Parse MCP responses into sighting SQL (Gmail/Calendar/Slack)
│   ├── resolve-sightings.sql  # B1-B5 resolution cascade + linkedin_connections auto-check
│   ├── update-people.sql      # Phase D: scores, sources, names, last_seen
│   ├── merge-people.sh        # 6-step merge protocol with snapshot
│   ├── finalize-run.sql       # Run bookkeeping (counts, timing)
│   ├── process-run.sh         # Single orchestrator: parse + resolve + update + finalize
│   ├── run-collect.sh         # Full collect pipeline: preflight + collect + process + report
│   ├── collect-sources.py     # MCP collection: Gmail/Calendar/Slack via SSE (no agent tokens)
│   ├── enrich-linkedin.py     # LinkedIn search: save raw results for agent review
│   ├── status.sh              # Database status report
│   ├── auto-merge.sh          # Auto-merge obvious duplicates (exact name + domain)
│   ├── preflight.sh           # Pre-flight env + DB check
│   └── test-pipeline.sh       # Integration tests
├── viewer/
│   └── index.html             # Single-file HTML viewer (sql.js WASM)
├── .env                         # Runtime config (gitignored) -- LC_MAX_PARTICIPANTS etc.
├── .env.example                 # Config template with documentation
├── schema.sql                  # SQLite schema definition
├── ARCH.md                     # This file
├── CLAUDE.md                   # Agent instructions and project rules
├── LICENSE                     # MIT
├── README.md                   # Setup and usage guide
├── ROADMAP.md                  # Phased milestones
├── SPEC.md                     # Product specification
└── TASKS.md                    # Task tracking
```

## Components

### Claude Code Commands (`.claude/commands/`)

The core "application logic." Each command is a prompt that instructs the agent what to do. There is no code to execute -- the agent reads the prompt and carries out the instructions using MCP tools and shell commands.

| Command | Purpose | MCP Dependencies |
|---------|---------|-----------------|
| `/collect` | Ingest contacts from communication channels | Google Workspace, Slack |
| `/enrich` | Find LinkedIn profiles, check connection status | Web search, LinkedIn MCP, linkedin_connections table |
| `/status` | Print database statistics | None (sqlite3 CLI) |

### Cursor Rules (`.cursor/rules/`)

Cursor-side agent playbooks. The `collect.mdc` rule is the primary "run" command for Cursor users.

| Rule | Purpose | Trigger |
|------|---------|---------|
| `collect.mdc` | Contact collection from all sources | User says "collect" or "run" |
| `standards.mdc` | Coding conventions | Always applied |

### MCP Servers (external, not in repo)

MCP servers configured in `.claude/mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor).

| Server | Transport | Used by | What it provides |
|--------|-----------|---------|-----------------|
| google-workspace | mcp-proxy SSE | `/collect`, `collect.mdc` | Gmail search/read, Calendar events + attendees |
| google-contacts | mcp-proxy SSE | `/collect`, `collect.mdc` | Google Contacts, Workspace directory lookup |
| slack | mcp-proxy SSE | `/collect`, `collect.mdc` | DMs, channel history, user profiles |
| linkedin | uvx stdio | `/enrich` | LinkedIn profile lookup, connection degree |
| Web search | -- | `/enrich` | Google search results for LinkedIn matching |

### SQLite Database (`data/contacts.db`)

Single-file database, accessed via `sqlite3` CLI. No ORM, no driver library.

### HTML Viewer (`viewer/index.html`)

Standalone HTML page that loads the SQLite file client-side using sql.js (SQLite compiled to WASM). No server, no build step. Open the file in a browser and select the `.db` file.

## Data Model

Nine tables. See `schema.sql` for full DDL with constraints and indexes.

### `people` table

The primary output. One row per deduplicated person.

```sql
id              INTEGER PRIMARY KEY
name            TEXT NOT NULL
email           TEXT UNIQUE
company         TEXT
company_domain  TEXT
linkedin_url    TEXT
linkedin_confidence  TEXT  -- high / medium / low
interaction_score    INTEGER   -- direct_points + group_points(cap 2) + multi_channel_bonus
channel_diversity    INTEGER   -- count of distinct direct interaction types
first_seen      TEXT  -- ISO timestamp
last_seen       TEXT  -- ISO timestamp
sources         TEXT  -- comma-separated: gmail, calendar, slack
status          TEXT  -- new / reviewed / connected / ignored
notes           TEXT
created_at      TEXT
updated_at      TEXT
```

### `sightings` table

Every raw contact appearance from a source. Preserves immutable `raw_*` fields for traceability. Replaces the old `interactions` table. Used for scoring, explainability, and debugging duplications.

```sql
id              INTEGER PRIMARY KEY
run_id          INTEGER  -- FK → runs.id
source          TEXT     -- gmail / calendar / slack
source_ref      TEXT     -- message_id, event_id, thread_ts
source_uid      TEXT     -- source-native unique ID (email or Slack User ID)
raw_name        TEXT     -- name as extracted (immutable)
raw_email       TEXT     -- email as extracted (immutable, may be NULL for Slack)
raw_company     TEXT     -- company as inferred (immutable)
raw_title       TEXT     -- job title if available (immutable)
raw_username    TEXT     -- source handle, e.g. Slack username (immutable)
interaction_type TEXT    -- email_sent / email_received / meeting / slack_dm / slack_channel
is_group        INTEGER -- 1 = group/broadcast (mailing list, large meeting, channel), 0 = direct
interaction_at  TEXT     -- when the interaction happened
context         TEXT     -- subject line, event title, channel name
person_id       INTEGER  -- FK → people.id (NULL until resolved)
match_method    TEXT     -- exact_email / exact_source_uid / fuzzy_name / agent_judgment / manual
match_confidence TEXT    -- high / medium / low
matched_at      TEXT     -- when resolution happened
created_at      TEXT
```

### `matching_rules` table

Explicit identity resolution rules. The agent writes rules here; they are applied automatically in future runs. Users can edit rules manually to correct mistakes.

```sql
id              INTEGER PRIMARY KEY
person_id       INTEGER  -- FK → people.id
identifier_type TEXT     -- email / slack_uid / name_domain
identifier_value TEXT    -- the identifier (email, Slack UID, or "name||domain")
source          TEXT     -- which source triggered this rule
created_by_run_id INTEGER -- FK → runs.id
confidence      TEXT     -- high / medium / low
notes           TEXT     -- agent reasoning (especially for name_domain rules)
created_at      TEXT
UNIQUE(identifier_type, identifier_value)
```

### `merge_log` table

Audit trail for person merges. Snapshots the deleted person as JSON before deletion.

```sql
id              INTEGER PRIMARY KEY
kept_person_id  INTEGER  -- FK → people.id (surviving person)
merged_person_id INTEGER -- the deleted person's former ID
merged_person_snapshot TEXT -- JSON of the deleted people row
reason          TEXT     -- why the merge happened
run_id          INTEGER  -- FK → runs.id
merged_at       TEXT
```

### `linkedin_searches` table

Every LinkedIn search attempt with candidates and choice. Even failed/skipped searches are recorded.

```sql
id              INTEGER PRIMARY KEY
person_id       INTEGER  -- FK → people.id
run_id          INTEGER  -- FK → runs.id
search_query    TEXT     -- the actual query string
candidates      TEXT     -- JSON array: [{url, name, headline, company}]
chosen_url      TEXT     -- selected URL, or NULL if no match
confidence      TEXT     -- high / medium / low
notes           TEXT     -- agent reasoning
searched_at     TEXT
```

### `slack_users` table

Cache of Slack user lookups. Avoids redundant `users_search` MCP calls across runs.

```sql
slack_uid       TEXT PRIMARY KEY  -- Slack User ID (e.g., U08KYTFTKMZ)
username        TEXT     -- Slack handle
real_name       TEXT     -- Display name
email           TEXT     -- Email from users_search
title           TEXT     -- Job title
fetched_at      TEXT
```

### `runs` table

Bookkeeping. One row per collection or enrichment run.

```sql
id               INTEGER PRIMARY KEY
started_at       TEXT
finished_at      TEXT
source           TEXT  -- gmail / calendar / slack / all / enrich
contacts_found   INTEGER
contacts_new     INTEGER
contacts_updated INTEGER
notes            TEXT
```

### `linkedin_connections` table

Imported from LinkedIn CSV export. Used to check connection status during enrichment.

```sql
id              INTEGER PRIMARY KEY
first_name      TEXT
last_name       TEXT
linkedin_url    TEXT UNIQUE
email           TEXT
company         TEXT
position        TEXT
connected_on    TEXT
created_at      TEXT
```

### `contact_names` table

Cached email-to-name lookups from Google Contacts directory. Avoids redundant `search_directory` MCP calls across runs.

```sql
email       TEXT PRIMARY KEY
name        TEXT
title       TEXT
fetched_at  TEXT
```

## Data Flow

```
                              collect-sources.py (zero agent tokens)
Gmail MCP ──┐                 ├── Slack cache seed (slack_users)
Cal MCP  ───┤──→ temp files ──┤── Name enrichment (contact_names cache)
Slack MCP ──┘                 └── Name backfill (people table)

process-run.sh (deterministic)
temp files ──→ parse-source.py ──→ sightings ──→ resolve-sightings.sql ──→ people
                                                 ├── matching_rules lookup
                                                 └── new rules (B4/B5)

run-collect.sh chains: preflight → collect-sources.py → process-run.sh → auto-merge → report

enrich-linkedin.py (saves raw results)
people ──→ search_people MCP ──→ data/tmp/linkedin/*.json ──→ agent reviews ──→ people.linkedin_url

LinkedIn CSV ──→ import-connections.sh ──→ linkedin_connections (auto-checked in resolve)

merge decisions ──→ merge_log (snapshot + audit)
                ──→ sightings + matching_rules reassigned to surviving person

matching_rules ──→ applied automatically on next /collect run

SQLite ──→ viewer/index.html (client-side via sql.js)
SQLite ──→ export-csv.sh ──→ CSV file
```
