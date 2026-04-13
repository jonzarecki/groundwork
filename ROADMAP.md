# Groundwork -- Roadmap

## Phase 1: Foundation (Day 1) -- DONE

Get the project skeleton working end-to-end with a single source.

- [x] Repo structure, SPEC.md, ROADMAP.md
- [x] SQLite schema (`schema.sql`) + init script
- [x] `/collect` command -- Gmail only (simplest source: email senders/recipients)
- [x] Basic deduplication (email-based exact match)
- [x] `/status` command -- print row counts and basic stats
- [x] Verify: run collect, inspect DB with `sqlite3`, see contacts

## Phase 2: All Sources (Day 2) -- DONE

Expand collection to all three input channels.

- [x] `/collect` adds Google Calendar support (meeting attendees)
- [x] `/collect` adds Slack support (DM partners, channel co-participants)
- [x] Scoring logic (meetings=3, emails_sent=2, emails_received=1, slack_dms=2, slack_channels=1)
- [x] Cross-source deduplication (same person in Gmail + Calendar + Slack -> one row)
- [x] Interactions table populated with evidence for each contact (replaced by sightings in Phase 6)
- [x] E2E tested collect pipeline (Gmail, Calendar, Slack via MCP -> SQLite)

## Phase 3: LinkedIn Enrichment (Day 3) -- DONE

Find LinkedIn profiles for collected contacts.

- [x] `/enrich` command -- `search_people` MCP as primary, web search as fallback
- [x] Confidence scoring (high/medium/low based on match quality)
- [x] Batch limit support (configurable via `LC_ENRICH_BATCH_SIZE`, default 10)
- [x] Skip already-enriched people
- [x] CSV export script
- [x] LinkedIn connections CSV import (viewer drag-and-drop + CLI script)
- [x] Auto-connect from linkedin_connections during resolution (zero MCP calls)
- [x] Live connection degree check via `get_person_profile` MCP
- [x] LinkedIn enrichment skill (`.cursor/skills/linkedin-enrich/`)

## Phase 4: Viewer (Day 4) -- DONE

Make the output browseable without SQL.

- [x] Single-file `viewer/index.html` using sql.js (WASM SQLite in browser)
- [x] Sortable columns: score, name, company, last seen, confidence
- [x] Filterable: by source, has LinkedIn, confidence level, status, full name
- [x] Clickable LinkedIn URLs (open in new tab)
- [x] Click-to-expand detail panel: raw sightings, matching rules, merge history, LinkedIn searches
- [x] LinkedIn CSV import walkthrough (3-step modal with drag-and-drop)
- [x] Ignore/unignore toggle per contact row
- [x] Auto-load database from server + save modified DB

## Phase 5: Polish + Open Source (Day 5) -- DONE

Get it ready for GitHub.

- [x] README.md with setup guide, weekly workflow, architecture table, recovery
- [x] CLAUDE.md with project conventions + full collect flow (agent-agnostic)
- [x] LICENSE (MIT)
- [x] .gitignore (data/, *.db, .env, mcp.json files)
- [x] `.env.example` with all config vars documented
- [x] 39 integration tests (`scripts/test-pipeline.sh`)
- [ ] Test the full flow from scratch on a clean setup
- [ ] First GitHub release

## Phase 6: Traceability & Matching Rules -- DONE

Add full audit trail for identity resolution to debug duplications.

- [x] Replace interactions with sightings table (raw capture with source_uid, raw_* fields)
- [x] Add matching_rules table (explicit identity rules: email, slack_uid, name_domain)
- [x] Add merge_log table (snapshot before delete, reason, reversibility)
- [x] Add linkedin_searches table (every search attempt logged with candidates)
- [x] Add slack_users cache table (eliminates redundant Slack MCP lookups)
- [x] Resolution protocol in CLAUDE.md: B1-B3 automatic rule lookup, B4 agent judgment, B5 new people
- [x] B1b fallback to people.email when matching_rules is empty (resilient for clean starts)
- [x] Auto-connect from linkedin_connections CSV during resolution
- [x] Deterministic scripts: parse-source.py, resolve-sightings.sql, update-people.sql, merge-people.sh, finalize-run.sql
- [x] Single orchestrator: process-run.sh (parse + resolve + update + finalize in one command)
- [x] Sighting dedup (skip if source_ref + source_uid already exists)
- [x] Collect commands reference scripts instead of inline SQL
- [x] Parallel MCP calls + 3-turn agent workflow
- [x] Configurable: LC_MAX_PARTICIPANTS, LC_SELF_EMAIL, LC_COLLECT_DAYS, LC_ENRICH_BATCH_SIZE
- [x] Viewer: expand row to see sightings, rules, merge history
- [x] E2E tested: auto-resolve on second run (102/102 via B1), merge protocol, name resolution

## Phase 7: Automated Auth Setup -- IN PROGRESS

Simplify MCP credential management so users never touch GCP console or manually copy tokens from browser dev tools. Core script (`local-automation-mcp/auth_setup.py`) and wrappers exist; several pieces need fixing/completing.

### What's done

- [x] Core `auth_setup.py` with CLI (`--slack`, `--google`, `--check`, `--env-file`)
- [x] Slack `xoxd` extraction from Chrome cookies via `pycookiecheat` (works reliably)
- [x] Slack `xoxc` extraction via LevelDB scan (finds tokens but they may be stale)
- [x] Slack `xoxc` extraction via AppleScript from running Chrome (works if user enables the Chrome setting)
- [x] Slack `xoxc` manual paste fallback with validation via `auth.test` API
- [x] Slack token validation (`auth.test` confirms workspace + user)
- [x] Google OAuth loopback flow with embedded client ID (code exchange, email detection)
- [x] Google OAuth manual code-paste fallback (for when loopback fails)
- [x] Google token freshness check (refresh token exchange)
- [x] Env file read/write preserving comments and order
- [x] Wrapper scripts: `local-automation-mcp/setup-auth.sh` and `groundwork/scripts/setup-auth.sh`
- [x] `setup.sh` step 5 integrates `--check` for token freshness
- [x] `CLAUDE.md` updated with auth setup documentation
- [x] Dependencies managed via `uv` in `local-automation-mcp/pyproject.toml`

### What needs fixing

- [ ] **Slack `xoxc` auto-extraction is unreliable** -- LevelDB scan finds stale tokens (current xoxc not persisted to disk by Slack SPA). Need a better primary strategy:
  - Option A: Playwright with persistent context (launch with user's Chrome profile when Chrome is closed)
  - Option B: Chrome DevTools Protocol connection to running Chrome (requires `--remote-debugging-port`)
  - Option C: Improve AppleScript path (prompt user to enable it once, then it's fully automated)
  - Current state: falls back to manual paste, which still beats the old fully-manual flow
- [ ] **Google OAuth `redirect_uri_mismatch`** -- the existing OAuth client is "Web Application" type, which rejects `http://localhost:PORT` redirects. Fixes:
  - Option A (recommended): Change OAuth client type to "Desktop App" in GCP console (one-time, 30s)
  - Option B: Register a fixed redirect URI (e.g., `http://localhost:8765/callback`) on the existing Web App client
  - Option C: Use the OOB (out-of-band) manual code flow permanently (works now but worse UX)
- [ ] **Docker restart after token update** -- `setup-auth.sh` calls `docker compose restart` but should use `docker compose up -d --force-recreate` to pick up new env vars (restart doesn't re-read `.env`)
- [ ] **End-to-end test** -- run the full flow on a clean machine: `setup-auth.sh` -> `setup.sh` -> `run-collect.sh` and verify all three sources collect successfully
- [ ] **Token expiry monitoring** -- Slack `xoxc`/`xoxd` tokens expire (session-based). Add a `--check` output that shows approximate token age and warns when likely stale
- [ ] **Chrome profile auto-detection** -- `pycookiecheat` defaults to "Default" profile but the Slack session lives in "Profile 1". Script should scan all profiles for Slack cookies

### Nice-to-have

- [ ] Launchd plist for periodic token refresh (macOS)
- [ ] Support Firefox cookie extraction (in addition to Chrome)
- [ ] Playwright-based fully automated `xoxc` extraction (zero manual steps)

## Phase 8: Headless / Scheduled Runs

Fully automated collect via `claude -p` (headless mode) for cron/scheduled execution.

- [ ] `scripts/collect-headless.sh` -- uses `claude -p` to make MCP calls, pipes to `process-run.sh`
- [ ] Handle Gmail/Slack pagination in headless prompts
- [ ] Graceful fallback when MCP auth expires (notify user, skip source)
- [ ] Cron wrapper: `scripts/collect-cron.sh` that runs headless + logs output
- [ ] Email/Slack notification after scheduled run (summary report)

**Exit criteria:** `crontab -e` with `0 8 * * 1 ./scripts/collect-cron.sh` runs every Monday at 8am, collects the week's contacts, and the user sees results in the viewer without opening an agent session.

## Future (not in scope for v1)

These are ideas from the research that are explicitly deferred:

- Additional sources (WhatsApp export, Twitter/X, Zoom, Outlook, Teams, GitHub)
- Relationship decay / staleness detection
- Meeting prep briefings
- "Who do I know at X?" queries
- Connection note drafting
- Shared team database mode
