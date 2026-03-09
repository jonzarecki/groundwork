# Linked Collector -- Roadmap

## Phase 1: Foundation (Day 1)

Get the project skeleton working end-to-end with a single source.

- [x] Repo structure, SPEC.md, ROADMAP.md
- [ ] SQLite schema (`schema.sql`) + init script
- [ ] `/collect` command -- Gmail only (simplest source: email senders/recipients)
- [ ] Basic deduplication (email-based exact match)
- [ ] `/status` command -- print row counts and basic stats
- [ ] Verify: run collect, inspect DB with `sqlite3`, see contacts

**Exit criteria:** Run `/collect`, get a `contacts.db` with real people from your Gmail, queryable with SQL.

## Phase 2: All Sources (Day 2)

Expand collection to all three input channels.

- [x] `/collect` adds Google Calendar support (meeting attendees)
- [x] `/collect` adds Slack support (DM partners, channel co-participants)
- [x] Scoring logic (meetings=3, emails_sent=2, emails_received=1, slack_dms=2, slack_channels=1)
- [x] Cross-source deduplication (same person in Gmail + Calendar + Slack → one row)
- [x] Interactions table populated with evidence for each contact (now replaced by sightings in Phase 6)

**Exit criteria:** Run `/collect`, see people from all three sources merged and scored correctly.

## Phase 3: LinkedIn Enrichment (Day 3)

Find LinkedIn profiles for collected contacts.

- [ ] `/enrich` command -- web search `site:linkedin.com/in "{name}" "{company}"`
- [ ] Confidence scoring (high/medium/low based on match quality)
- [ ] Batch limit support (`/enrich 20` to control search quota usage)
- [ ] Skip already-enriched people
- [ ] CSV export script

**Exit criteria:** Run `/enrich`, see LinkedIn URLs with confidence scores populated in the DB.

## Phase 4: Viewer (Day 4)

Make the output browseable without SQL.

- [ ] Single-file `viewer/index.html` using sql.js (WASM SQLite in browser)
- [ ] Sortable columns: score, name, company, last seen, confidence
- [ ] Filterable: by source, has LinkedIn, confidence level
- [ ] Clickable LinkedIn URLs (open in new tab)
- [ ] Show interaction evidence on row expand/click

**Exit criteria:** Open `viewer/index.html` in a browser, see the full contact table, click through to LinkedIn profiles.

## Phase 5: Polish + Open Source (Day 5)

Get it ready for GitHub.

- [ ] README.md with setup guide (MCP prerequisites, init, usage)
- [ ] CLAUDE.md with project conventions
- [ ] LICENSE (MIT)
- [ ] .gitignore (ignore data/, *.db)
- [ ] Test the full flow from scratch on a clean setup
- [ ] First GitHub release

**Exit criteria:** Someone with Claude Code + Google/Slack MCPs can clone the repo, follow the README, and have a working contact list within 15 minutes.

## Phase 6: Traceability & Matching Rules

Add full audit trail for identity resolution to debug duplications.

- [ ] Replace interactions with sightings table (raw capture with source_uid, raw_* fields)
- [ ] Add matching_rules table (explicit identity rules: email, slack_uid, name_domain)
- [ ] Add merge_log table (snapshot before delete, reason, reversibility)
- [ ] Add linkedin_searches table (every search attempt logged with candidates)
- [ ] Resolution protocol in CLAUDE.md: B1-B3 automatic rule lookup, B4 agent judgment creates new rules
- [ ] Update collect commands to sighting-first workflow (Phase A-D)
- [ ] Update enrich command to log all search attempts
- [ ] Viewer: expand row to see sightings, rules, merge history

**Exit criteria:** Run `/collect` twice, inspect `matching_rules` to see all identity mappings, inspect `sightings` to trace raw data back to source, inspect `merge_log` for any dedup decisions.

## Future (not in scope for v1)

These are ideas from the research that are explicitly deferred:

- Configurable time windows per source
- "Status" column workflow (new → reviewed → connected → ignored)
- Periodic/scheduled runs (cron wrapper)
- Additional sources (WhatsApp export, Twitter/X, Zoom)
- Relationship decay / staleness detection
- Meeting prep briefings
- "Who do I know at X?" queries
- Connection note drafting
