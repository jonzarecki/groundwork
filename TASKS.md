# Tasks

## Completed
- [x] Project scaffold (SPEC.md, ROADMAP.md, CLAUDE.md, README.md)
- [x] SQLite schema (people, interactions, runs tables + indexes)
- [x] `/collect` command prompt (Gmail + Calendar + Slack ingestion + dedup)
- [x] `/enrich` command prompt (LinkedIn web search + confidence scoring)
- [x] `/status` command prompt (database stats overview)
- [x] Shell scripts (init-db.sh, export-csv.sh)
- [x] HTML viewer (single-file, sql.js, sortable/filterable table)
- [x] AI-native tooling (.claude/ config, hooks, skills, .context/)
- [x] Setup script (scripts/setup.sh -- init DB + configure MCP servers for Cursor)
- [x] Cursor collect rule (.cursor/rules/collect.mdc -- agent collection playbook)
- [x] MCP usage rules in CLAUDE.md (Google auth, Slack handling, failure protocol)
- [x] LinkedIn connection status: schema, import script, MCP setup, enrich integration
- [x] E2E tested collect pipeline (Gmail, Calendar, Slack via MCP → SQLite)

## In Progress
(none)

## Backlog

### Traceability (Phase 6) -- mostly complete
- [x] Schema migration: sightings, matching_rules, merge_log, linkedin_searches tables
- [x] Resolution protocol in CLAUDE.md (single source of truth)
- [x] Rewrite collect.md / collect.mdc for sighting-first workflow (Phase A-D)
- [x] Rewrite enrich.md to log searches in linkedin_searches
- [x] Viewer: sighting detail panel per person (raw sightings, rules, merge history)
- [x] DB migration: interactions -> sightings, initial matching_rules, non-person cleanup
- [x] Test: merge two people (Mat Kowalski), verify merge_log snapshot and rule reassignment
- [x] Test: resolve auto-derived names via Slack users_search (Roy Nissim, Adel Zaalouk)
- [x] E2E test: full /collect run with new sighting-first pipeline (run 3: 102 sightings, 12 new people, 108 rules)
- [x] E2E test: second /collect run to verify matching_rules auto-resolve (run 4: 102/102 via B1, zero fallback)
- [x] Protocol fixes: B1b fallback, calendar invite filtering, LC_MAX_PARTICIPANTS, Phase D batch update

### Existing
- [x] Test `/collect` against real Gmail MCP (run 3: 16 sightings, calendar invite filtering, mailing list handling)
- [x] Test `/collect` against real Calendar MCP (run 3: 72 sightings, LC_MAX_PARTICIPANTS skip)
- [x] Test `/collect` against real Slack MCP (run 3: 14 sightings, DMs+MPDMs, slack_uid rules)
- [x] Test `/enrich` against real web search (run 5: Roy Nissim + Adel Zaalouk found, Jenny Yi failed, all logged)
- [x] Test `/status` output formatting
- [x] Handle edge cases: no-reply addresses, mailing lists, calendar bots, resource rooms, calendar invites via Gmail
- [ ] Test viewer with a populated database
- [ ] Test export-csv.sh with a populated database
- [ ] Tune scoring weights based on real data
- [ ] Add interaction count column to viewer

## Follow-up Ideas
- [ ] Configurable time windows per source
- [ ] Status column workflow (new → reviewed → connected → ignored) in viewer
- [ ] Periodic/scheduled runs via cron wrapper
- [ ] Additional sources: WhatsApp export, Twitter/X, Zoom
- [ ] Connection note drafting from interaction context
- [ ] "Who do I know at X?" search command
