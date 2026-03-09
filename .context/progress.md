# Progress

## Session 3 -- Traceability & Matching Rules
- Replaced `interactions` table with `sightings` (raw contact appearances with immutable raw_* fields, source_uid, raw_username, match_method, match_confidence)
- Added `matching_rules` table (explicit identity resolution rules: email, slack_uid, name_domain with UNIQUE constraint)
- Added `merge_log` table (audit trail for person merges with JSON snapshot of deleted person)
- Added `linkedin_searches` table (every enrichment search attempt logged with candidates JSON and agent reasoning)
- Added Resolution Protocol to CLAUDE.md: Phase A (insert sighting) → B (resolve via matching_rules cascade B1-B5) → C (link sighting) → D (update person). Single source of truth for both collect.md and collect.mdc.
- Rewrote collect.md Steps 4-5 and collect.mdc Steps 5-6 for sighting-first workflow
- Updated enrich.md to INSERT into linkedin_searches for every search attempt (including skips/failures)
- Rewrote SPEC.md data model (7 tables), dedup rules (B1-B5 cascade), LinkedIn matching (3-phase with logging)
- Rewrote ARCH.md data model (7 tables) and data flow diagram (sighting-first pipeline with matching_rules)
- Added Phase 6 to ROADMAP.md (Traceability & Matching Rules)
- Updated TASKS.md with traceability backlog items
- Added click-to-expand detail panel to viewer/index.html (sightings, matching rules, merge history, LinkedIn searches per person)
- Schema validated against sqlite3

## Session 2 -- LinkedIn Connection Status + E2E Testing
- Added linkedin_connections table to schema.sql and migrated existing DB
- Created scripts/import-connections.sh (parse LinkedIn CSV export, upsert into DB)
- Added linkedin-scraper-mcp to .claude/mcp.json and .cursor/mcp.json
- Updated setup.sh: LinkedIn MCP install check (step 7), LinkedIn CSV import with auto-detect (step 8)
- Updated /enrich command: check linkedin_connections before web search, mark status='connected' when matched, optional live MCP verification
- Updated collect.mdc: added step 6c for cross-referencing linkedin_connections during agent review
- Documented connected status semantics and LinkedIn MCP in CLAUDE.md
- Updated ARCH.md with linkedin_connections table, LinkedIn MCP server, updated data flow
- E2E tested full collect pipeline: Gmail (250 messages), Calendar (100 events), Slack (104 users) → 599 contacts
- Identified quality issues: 432 auto-derived names, timestamp parsing bugs, calendar resource rooms as contacts

## Session 1 -- Setup & Run Commands
- Created scripts/setup.sh (init DB + create .cursor/mcp.json from .claude/mcp.json)
- Created .cursor/rules/collect.mdc (Cursor agent collection playbook: Gmail, Calendar, Slack, Google Contacts → dedup → SQLite)
- Added MCP usage rules to CLAUDE.md (Google auth assumption, Slack thread-reading protocol, MCP failure handling)
- Updated ARCH.md file tree with new files (setup.sh, collect.mdc, .cursor/mcp.json)
- Updated TASKS.md with completed setup/run tasks
- Added .cursor/mcp.json to .gitignore (contains auth tokens)

## Session 0 -- Project Setup
- Created repo at ~/Projects/linked-collector/
- Wrote SPEC.md (use cases, data model, scoring, commands)
- Wrote ROADMAP.md (5 phases: foundation → all sources → enrichment → viewer → polish)
- Wrote CLAUDE.md (project rules, dedup logic, scoring weights, confidence levels)
- Created SQLite schema (people, interactions, runs + indexes)
- Wrote /collect command (Gmail + Calendar + Slack ingestion, dedup, scoring)
- Wrote /enrich command (LinkedIn web search, confidence scoring, batch limits)
- Wrote /status command (database stats, top unlinked contacts, recent runs)
- Created init-db.sh and export-csv.sh scripts
- Built single-file HTML viewer with sql.js (dark theme, sortable, filterable)
- Copied research sources to docs/research/
- Applied AI-native cookiecutter (.claude/ config, hooks, skills, .context/, .cursor/rules)
- Created ARCH.md and TASKS.md
