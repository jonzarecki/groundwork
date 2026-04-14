# Progress

## Session -- Ranking v3: Five Systematic Biases Fixed (2026-04-14)
- **Email thread dedup**: `direct_provider.py` now emits `Thread ID:` in Gmail text blocks; `parse-source.py` uses `threadId` as `source_ref` for `email_received` so an entire Re: chain = 1 sighting
- **Bot leakage fix**: Added `SKIP_SUBJECT_PATTERNS` in `parse-source.py` blocking `[JIRA]`, `[Jira]`, Google Drive share/invite notifications
- **Slack DM date-bucketing**: `_parse_slack_dm_history` now uses `{channel_id}_{YYYY-MM-DD}` as `source_ref`, scoped dedup per (uid, channel, date) — each active day = 1 new sighting
- **New scoring formula v3** (`update-people.sql`, `merge-people.sh`): strong/weak signal pools with unified 3-pt weak cap, size-aware meeting weights (1:1=5, small=4, medium→weak), slack_dm=4, per-thread email weights, +5 has_direct_bonus, multiplicative div_multiplier (1×/1.5×/2.5×/4×)
- **Per-source history windows**: `LC_SLACK_DM_DAYS` (default 30), `LC_CALENDAR_DAYS`, `LC_GMAIL_DAYS` env vars; wired through `collect-sources.py` and `direct_provider.py`
- Score distribution after recompute: weak-only max=3, strong-direct min=6 (guarantee maintained); top tier 60+ contains 8 connected people; 16-30 range is 88% connected

## Session -- Onboarding UX + always-enrich + always-webserver + /start command (2026-04-14)
- Rewrote `.claude/commands/install.md`: 3-step required path (deps → email → Google OAuth) → first run → optional Slack/LinkedIn value-adds offered post-collection; `--check` is now the very first action; clean single-line finish
- Updated `CLAUDE.md`: manual setup section separates required vs optional steps; intro copy updated to reflect 3-step path

## Session -- Always-enrich + always-webserver + /start command (2026-04-14)
- Updated `.claude/commands/collect.md`: LinkedIn enrichment is now always run (not optional); added Phase 6 to always launch the viewer at http://localhost:8080 after collection
- Created `.claude/commands/start.md`: new all-in-one post-onboarding command — collect + enrich + viewer in one step
- Updated `.claude/commands/install.md`: Step 8 now runs full collect+enrich+viewer and tells users to say `/start` going forward
- Updated `CLAUDE.md`: surfaced `/start` prominently in Quick start and Collect flow sections

## Session -- Collect + Enrich (2026-04-13)
- Fixed `run-collect.sh` to respect `LC_PROVIDER` env var (pass `--provider` to `collect-sources.py`)
- Added `LC_PROVIDER=mcp` to `.env` to use Docker MCP stack
- Run #15: 653 sightings (Gmail 172, Calendar 438, Slack 43), 108 new contacts, 2 auto-merges (Itamar Heim, David Williams), 4 incomplete names (IBM external contacts, unresolvable)
- Run #16+17: LinkedIn enrichment — 95 → 129 profiles (+34). 3 new 1st-degree connections: Nir Rozenbaum, Amit Oren, Nir Yechiel (status=connected)
- Contacts: 505 → 613 total. LinkedIn: 77 → 129 (was 95 at end of previous session)

## Session -- LinkedIn Enrichment E2E + Website Deploy
- Processed 17 pending LinkedIn result files from `data/tmp/linkedin/` (run #12):
  - 11 validated and saved: Christoph Görn, Adel Zaalouk, Adam Bellusci, Jeff DeMoss, Roy Nissim (1st degree → connected), Peter Double, Jenny Yi, Lindani Phiri, Bryon Baker, Naina Singh, Daniele Zonca
  - 6 failed/no-match: Noy Itzikowitz (404), Kezia Cook (404), Christine Bryan (404), Jamie Land (404), Myriam Fentanes Gutierrez (no results), Jessie Kaempf (parentheses broke search)
- Ran 29 fresh LinkedIn searches via MCP `search_people` tool:
  - 28 found and validated: Vanessa Martini, Kevin Howell, Karanbir Singh, Pau Garcia Quiles, Sanjeev Rampal, Edson Tirelli, Jessica Forrester, Huamin Chen, Senan Zedan, Lindsey Frazier, Pierangelo Di Pilato, Christian Vogt, Kris Verlaenen, Jay Koehler, Justin Sun (medium), Chaitany Patel, Vanshika Verma (medium, name mismatch), Jessie Kaempf (Huff) - retried without parentheses
  - 2 no-results: Srihari Venkataramaiah, Zack Bodnar
- Net result: 77 → 95 LinkedIn profiles (+18), all logged in `linkedin_searches` table
- Deployed website: `python3 scripts/server.py` running at http://localhost:8080/viewer/index.html

## Session -- Direct API Provider (No-MCP Installation)
- Created `scripts/providers/` package with provider abstraction:
  - `mcp_provider.py`: extracted MCP logic from collect-sources.py (unchanged behavior)
  - `direct_provider.py`: Google OAuth + Slack browser cookie direct API calls
  - `linkedin_direct.py`: LinkedIn Voyager API via `linkedin-api` library + Chrome `li_at` cookie
- Refactored `scripts/collect-sources.py`: adds `--provider direct|mcp` flag (default: direct); lazy-imports the right provider module; same orchestration logic regardless of provider
- Refactored `scripts/enrich-linkedin.py`: adds `--provider direct|mcp` flag; direct mode uses `linkedin-api` (no uvx/Playwright/Chromium)
- Created `scripts/setup-auth.py`: self-contained auth wizard -- Google OAuth flow (client_secret.json), Slack xoxd/xoxc cookie extraction from Chrome via pycookiecheat + boot_data scrape, LinkedIn li_at extraction; replaces `setup-auth.sh` + `local-automation-mcp` dependency
- Created `scripts/import-sources.py`: offline import mode -- parses Google Takeout .mbox, Calendar .ics, Slack export JSON/ZIP into same intermediate format; no auth required
- Created `pyproject.toml`: dependency groups -- `[google]`, `[slack]`, `[linkedin]`, `[mcp]`, `[direct]` (all direct), `[all]`
- Updated `scripts/setup.sh`: removed hardcoded Python path, fixed stale DB table check (interactions→sightings), provider-aware setup flow, removed local-automation-mcp dependency for direct provider
- Updated `.env.example`: removed personal email, added `LC_PROVIDER=direct`, `LC_SLACK_WORKSPACE`
- Fixed `parse-source.py`: removed hardcoded `LC_SELF_EMAIL` default
- Updated `.gitignore`: added `data/imports/` pattern
- Net result: new users can install with `pip install -e ".[direct]"` + `python3 scripts/setup-auth.py` instead of Docker + sibling repo + MCP proxy

## Session -- Agent-to-Script Migration
- Created `scripts/run-collect.sh`: single orchestrator that chains preflight → run record → collect-sources.py → process-run.sh → auto-merge → formatted report. Agent goes from 4+ Bash calls to 1.
- Created `scripts/enrich-linkedin.py`: queries DB for unenriched contacts, calls `search_people` via LinkedIn MCP SSE, saves raw responses to `data/tmp/linkedin/` for agent review. Separates deterministic search from judgment-based evaluation.
- Created `scripts/status.sh`: replaces 8 agent SQL queries with formatted output in one Bash call.
- Created `scripts/auto-merge.sh`: detects and merges unambiguous duplicates (exact name + domain match), supports `--dry-run`.
- Added name backfill to `collect-sources.py`: queries people with incomplete names, looks up via Google Contacts `search_directory`, updates DB directly.
- Added `contact_names` table as cache for Google Contacts lookups (email→name). First run: ~60s for ~400 lookups. Subsequent runs: near-instant (only new emails).
- Added Slack directory seed: fetches `slack://redhat/users` MCP resource to bulk-populate `slack_users` cache when count < 100.
- Simplified all command files: `collect.md`, `enrich.md`, `status.md`, `collect.mdc` now wrap script calls instead of multi-step agent instructions.
- Updated `CLAUDE.md` with new script-based workflow, `ARCH.md` with new scripts and `contact_names` table, `schema.sql` with 9th table.
- Net result: agent's role is now (1) run orchestrator, (2) review LinkedIn candidates, (3) handle flagged items. Everything else is code.

## Session -- Script-based MCP Collection
- Created `scripts/collect-sources.py`: connects directly to MCP proxy via SSE using Python MCP SDK, replaces ~80 agent MCP calls with 1 Bash command
- Gmail collection: search + metadata fetch with tighter query filters, capped at 3 pages (75 messages max)
- Slack collection: single `conversations_search_messages(filter_users_with=<UID>)` call replaces 70 `conversations_history` calls. Strips Text column to prevent CSV breakage.
- Calendar collection: unchanged (1 call, detailed=true)
- Moved temp files from `/tmp/lc_*.txt` to `data/tmp/lc_*.txt` (inside project dir, gitignored)
- Updated `process-run.sh`: defaults to `data/tmp/` paths, parse logs also in `data/tmp/`
- Created `scripts/preflight.sh`: env check + DB stats for auto-accept in Claude Code
- Simplified `.claude/commands/collect.md` and `.cursor/rules/collect.mdc`: Phase 1 is now a single script call
- Updated `CLAUDE.md`: documented script-based collection, updated Slack MCP handling section
- Cleaned up `.claude/settings.json`: removed `cp` rules, added `python3 scripts/*`
- Updated `setup.sh`: creates `data/tmp/`, checks Python MCP SDK availability
- Live-tested: 8 seconds total (vs 7+ minutes agent-driven), all 3 sources, 153 sightings, compatible with parse-source.py
- Impact: 0 agent MCP calls in Phase 1, 0 source tokens in context, Slack calls 70 → 1-2

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
- Created repo at ~/Projects/groundwork/
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
