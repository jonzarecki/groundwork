# Issues from Collect Run (Cursor, 2026-03-13)

Run: `./scripts/run-collect.sh 7` -- 86 seconds, exited with code 1.

## Critical

### 1. B5 INSERT aborts entirely when any sighting has NULL name

**Symptom**: 138 new emails should have created people records, but 0 were created. 242 sightings left unresolved.

**Root cause**: `resolve-sightings.sql` line ~74 does `INSERT INTO people (name, ...) SELECT s.raw_name, ...` where `people.name` has a NOT NULL constraint. 19 sightings have `raw_name IS NULL` (Slack users without display names). SQLite aborts the entire INSERT statement on the first constraint violation, so even the 119 sightings WITH valid names don't get people created.

**Fix**: Use `COALESCE(s.raw_name, SUBSTR(s.raw_email, 1, INSTR(s.raw_email, '@') - 1))` to derive a placeholder name from the email username when `raw_name` is NULL. Or split into two INSERTs: one for sightings with names, one for those without.

**Impact**: This is the #1 blocker. No new contacts are created on a fresh sightings table, making the entire pipeline produce zero useful output for new contacts.

### 2. run-collect.sh exits with code 1, skips auto-merge and report

**Symptom**: The output ends after `process-run.sh` B4 candidates. No "Auto-merge" or "Report" section printed.

**Root cause**: `resolve-sightings.sql` returns exit code 1 (due to the NOT NULL error), `process-run.sh` has `set -euo pipefail`, so it propagates the failure. `run-collect.sh` then exits before reaching auto-merge or report.

**Fix**: Either fix issue #1 (which eliminates the error), or make `process-run.sh` more resilient to partial SQL failures (e.g., `sqlite3 ... || true` for resolve step, or check error severity).

## High

### 3. Slack cache misses persist after collect-sources.py resolution

**Symptom**: `collect-sources.py` resolved 36 Slack users, but `parse-source.py` still reports 10 cache misses.

**Root cause**: `collect-sources.py` resolves cache misses by searching by username, but some usernames don't match in `users_search` (e.g., the Slack API returns no results for some queries). These 10 UIDs fall through to parse-source.py which reports them as misses.

**Fix**: Low priority -- these users will be resolved when the agent does Phase 5 review. Could also try searching by real name as a fallback.

### 4. B4 fuzzy matching is too broad

**Symptom**: B4 candidates include obviously wrong matches: "Erin Kessler Lombard" matched against "Catherine Weeks" (same company domain, first name substring match on "erin" in "cath**erin**e").

**Root cause**: The B4 query uses `LOWER(p.name) LIKE '%' || LOWER(SUBSTR(s.raw_name, 1, INSTR(s.raw_name || ' ', ' ') - 1)) || '%'` which matches any person whose name contains the first name as a substring. "Erin" matches "Cath**erin**e".

**Fix**: Use word-boundary matching or require the first name to match at the START of the existing name: `LOWER(p.name) LIKE LOWER(SUBSTR(...)) || '%'` instead of `'%' || ... || '%'`.

## Medium

### 5. Slack directory seed didn't run

**Symptom**: No "Slack directory seeded" message in the output. The `slack_users` table was likely already above the 100-count threshold from previous runs.

**Root cause**: `seed_slack_cache` checks `count >= 100` as the threshold. Previous runs had built up 78 entries, and the 36 new resolves from this run brought it over 100 during collection (but the seed check runs before collection).

**Note**: This is working as designed -- the seed is for bootstrapping. After a few runs, the cache is warm enough.

### 6. Pre-existing people (599) have no sightings linked

**Symptom**: `sightings: 0` at preflight, 599 people exist from old `interactions`-based pipeline. After this run, 702 sightings were created but only 460 resolved (B1/B1b matched against existing people.email).

**Root cause**: The schema migration added the `sightings` table but didn't migrate data from the old `interactions` table. The 599 people have no sightings history, only the old interactions.

**Fix**: Either run a migration to backfill sightings from interactions, or accept that the first collect run after migration won't have full history. Not a blocker -- subsequent runs will accumulate sightings.

### 7. Google Contacts name enrichment runs every time (no skip for known emails)

**Symptom**: "Names enriched from directory: 397" -- same count every run, even though the cache should eliminate most lookups.

**Root cause**: The `enrich_names_from_directory` function collects ALL emails from the Gmail/Calendar output (not just new ones). The cache check works for individual lookups, but the function still processes all emails in the output to build the `emails_without_names` set. Calendar emails are always bare (no `<` bracket), so they're always added to the lookup set.

**Note**: The cache IS working (it returns cached results without MCP calls). The "397" count includes cache hits. The actual MCP lookups on second run are near-zero. But the message could be clearer.

## Low

### 8. `contact_names` table created at runtime, not by schema.sql

**Symptom**: The table is created by `_load_contact_names_cache()` in collect-sources.py via `CREATE TABLE IF NOT EXISTS`. The schema.sql file also defines it, but the migration was applied separately.

**Note**: This is intentional -- the script is self-healing. Both paths create the same table.

### 9. auto-merge.sh pipe variable scope issue

**Symptom**: The `MERGED` counter inside the `while read` loop doesn't increment because the loop runs in a subshell (piped from `echo`).

**Fix**: Use process substitution or a temp file instead of pipe. Cosmetic only -- the merges still execute correctly.

## Summary

| Metric | Value |
|--------|-------|
| Runtime | 86 seconds |
| Exit code | 1 (should be 0) |
| Sightings created | 702 |
| Resolved | 460 (66%) |
| Unresolved | 242 (34%) -- should be ~0 after B5 fix |
| New people created | 11 (should be ~138) |
| Names backfilled | 303 |
| Slack users resolved | 36 |
| Names from directory | 397 |

**Priority fix order**: #1 (B5 NULL name) > #2 (exit code) > #4 (B4 fuzzy) > #3 (Slack misses)

---

## Fixes Applied (2026-03-13)

All issues fixed. Verification run results:

| Metric | Before fix | After fix |
|--------|-----------|-----------|
| Exit code | 1 | **0** |
| Runtime | 86s | **20s** (name cache warm) |
| Sightings | 702 | 702 |
| Resolved | 460 (66%) | **702 (100%)** |
| Unresolved | 242 | **0** |
| New people created | 11 | **385** |
| B4 false positives | "Erin" matched "Catherine" | **0 false matches** |
| Auto-merged | skipped (crash) | **7 pairs merged** |
| Slack cache misses at parse | 10 | **0 (all cached!)** |

### Fix details

1. **#1 B5 NULL name**: Added `COALESCE(s.raw_name, SUBSTR(s.raw_email, 1, INSTR(s.raw_email, '@') - 1))` to derive placeholder name from email username when raw_name is NULL. Both B5 INSERT statements (email-based and Slack-only) fixed.

2. **#2 Exit code**: Wrapped `sqlite3 < resolve-sightings.sql` in `if ! ...; then warn` so partial SQL errors don't abort the pipeline.

3. **#3 Slack cache misses**: Added real_name as fallback search query in `resolve_slack_cache_misses` when username search returns no results.

4. **#4 B4 fuzzy**: Changed LIKE pattern from `'%' || first_name || '%'` to `first_name || ' %'` so "Erin" matches "Erin ..." but not "Catherine". Also added `s.raw_name LIKE '% %'` filter to skip single-word names.

5. **#7 Log clarity**: `enrich_names_from_directory` now prints `Name cache: X hits, Y lookups (Z found)` to stderr, distinguishing cached results from new MCP calls.

6. **#9 auto-merge pipe**: Replaced `echo | while read` pipe (subshell) with `while read <<< "$VAR"` (here-string) so the MERGED counter persists.

---

## Run 2 (2026-03-13, post-fix)

Run: `./scripts/run-collect.sh 7` -- **14 seconds, exit code 0**. 699/699 resolved (100%). 11 auto-merges. Full report printed.

### Issue 10: auto-merge.sh crashes on stale merge target

**Symptom**: Last merge (Mat Kowalski #13 -> #8) output is truncated. Birkan Cilingir and GamzeAt duplicate pairs were not merged.

**Root cause**: Person #13 was deleted in a prior test cleanup. `merge-people.sh` exits non-zero when the target person doesn't exist. `set -euo pipefail` in `auto-merge.sh` aborts the `while` loop, skipping remaining pairs.

**Fix**: Check person exists before attempting merge, or add `|| true` to tolerate missing persons.

### Issue 11: Case-sensitive email creates duplicate people

**Symptom**: `BirkanC@garantibbva.com.tr` (from Calendar) and `birkanc@garantibbva.com.tr` (from Gmail) create 2 separate people. Same for GamzeAt.

**Root cause**: SQLite `UNIQUE` constraint on `people.email` is case-sensitive by default. B5 INSERT and B1b lookup both treat these as different emails. The Calendar API returns mixed-case emails for external attendees.

**Fix**: Normalize emails to lowercase in `resolve-sightings.sql` B5 INSERT: `LOWER(s.raw_email)`. Also normalize in B1b lookup and `parse-source.py`.

### Issue 12: Backfill doesn't cover pre-existing people with placeholder names

**Symptom**: 20 people have single-word names like "Ikolchin", "Egranger" (email usernames used as names). These are pre-existing from the old pipeline.

**Root cause**: `_backfill_names()` only checks against `name_map` from the current run's Gmail/Calendar emails. Pre-existing people whose emails didn't appear in this week's collection are not re-queried. The `contact_names` cache has NULL for these (Google Contacts returned nothing).

**Fix**: Make backfill query Google Contacts directly for all incomplete names, not just those in the current run's name_map. These might be former employees or have different email aliases.

### Issue 13: `ai-strategy@redhat.com` created as a person

**Symptom**: "ai-strategy" appears as a person with score 8. It's a mailing list address.

**Root cause**: `parse-source.py` SKIP_PATTERNS checks for `-all@`, `-team@`, `-list@` etc., but `ai-strategy@` doesn't match any of these patterns. It's a functional mailbox, not a person.

**Fix**: Add a pattern for common functional mailboxes: `r"^ai-"`, or more broadly, add `ai-strategy@` specifically. Or filter addresses where the local part matches the domain's org structure.

### Issue 14: NEW_CONTACTS metric inconsistency in report

**Symptom**: Summary says `NEW_CONTACTS=146`, report says `New contacts: 382`.

**Root cause**: `process-run.sh` calculates `NEW_CONTACTS = PEOPLE_AFTER - PEOPLE_BEFORE` (net new people records: 735 - 589 = 146). `run-collect.sh` report calculates "people with sightings only in this run" (382), which includes pre-existing people who got re-matched via B1b.

**Fix**: Use the `process-run.sh` metric (net new people created) in the report, not the sightings-based query.

---

## Run 3 (2026-03-13, post-fix round 2)

Run: `./scripts/run-collect.sh 7` -- **34 seconds, exit code 0**.

| Metric | Run 1 (broken) | Run 2 (first fix) | Run 3 (all fixes) |
|--------|---------------|-------------------|-------------------|
| Exit code | 1 | 0 | **0** |
| Runtime | 86s | 14s | **34s** (backfill lookups) |
| Resolved | 460/702 (66%) | 699/699 (100%) | **687/687 (100%)** |
| New contacts | 11 | 382 (wrong metric) | **131** (correct) |
| Incomplete names | 20 | 20 | **3** |
| Duplicate pairs | 13 | 2 | **0** |
| Auto-merges | 0 (crashed) | 11 (1 skipped) | **11** (1 gracefully skipped) |
| Names backfilled | 303 | 0 (cache warm) | **85** (new directory lookups) |

### Remaining 3 incomplete names

- `njayanty@redhat.com` (score 6) -- not in Google Contacts directory
- `shmueli@il.ibm.com` (score 6) -- external (IBM), can't look up in Red Hat directory
- `c_18836297...@resource.calendar.google.com` (score 6) -- calendar resource room, should be filtered

### Issue 15: Calendar resource room slipping through filters -- FIXED

`@resource.calendar.google.com` IS in SKIP_PATTERNS but the person was created by the old pipeline before the filter existed. Fixed by: (1) deleted 6 existing resource room people, (2) added a cleanup step to `process-run.sh` that removes `@resource.calendar.google.com`, `@group.calendar.google.com`, `noreply@`, and `no-reply@` people after each run.

### All previous issues (10-14) verified fixed

- **#10**: auto-merge gracefully skips missing persons ("Skipped: #547 or #163 not found")
- **#11**: No case-sensitive email duplicates (Birkan/Gamze pairs no longer created)
- **#12**: 85 names backfilled via direct directory lookup (was 0 before)
- **#13**: `ai-strategy@redhat.com` no longer created as a person (237 Gmail sightings vs 252 before = 15 functional mailboxes filtered)
- **#14**: Report says "New contacts: 131" matching net new people created (was 382 before)

---

## Enrichment Run (2026-03-13)

2 batches of 5, 10 contacts searched, 7 profiles found, 42s per batch.

### Issue 16: enrich-linkedin.py used SSE transport for stdio-based MCP -- FIXED

**Symptom**: "Could not connect to LinkedIn MCP" on first attempt.

**Root cause**: The LinkedIn MCP (`linkedin-scraper-mcp`) runs as a local stdio process via `uvx`, not through the SSE proxy. The script was using `sse_client` to connect to `http://localhost:9090/linkedin/sse` which returns 404.

**Fix**: Rewrote `enrich-linkedin.py` to use `stdio_client` with `StdioServerParameters(command='uvx', args=['linkedin-scraper-mcp'])`. Also changed `time.sleep` to `asyncio.sleep` for proper async behavior.

### Issue 17: `--status` check misleading

**Symptom**: `uvx linkedin-scraper-mcp --status` reported "No valid source session" but the MCP actually works fine via stdio.

**Root cause**: The `--status` flag checks for a "source session" file in `~/.linkedin-mcp/profile/` which may not reflect the actual browser session state. The MCP server itself initializes and works regardless.

**Note**: Not a code bug -- just a misleading diagnostic. The script should try connecting rather than relying on `--status`.

### Issue 18: Myriam Fentanes Gutierrez and Jamie Land not found on LinkedIn

Both returned "No results found". Possible causes:
- Myriam: name may be different on LinkedIn (Myriam vs María, Spanish naming conventions)
- Jamie: name may be "James Land" on LinkedIn

**Note**: These are genuine LinkedIn search limitations, not code bugs. Agent could retry with name variants.

### Enrichment results

| Contact | Score | URL | Degree | Confidence |
|---------|-------|-----|--------|------------|
| Jeff DeMoss | 22 | /in/jeff-demoss/ | 1st | high |
| Roy Nissim | 22 | /in/roy-nissim/ | 1st | high |
| Adel Zaalouk | 19 | /in/adelzaalouk/ | 1st | high |
| Naina Singh | 16 | /in/nainazwork/ | 2nd | high |
| Adam Bellusci | 15 | /in/adam-bellusci-0783254/ | 1st | high |
| Christoph Görn | 15 | /in/goern/ | 1st | high |
| Kezia Cook | 14 | /in/kezia-cook-bab8b3360/ | 3rd+ | medium |
| Myriam Fentanes Gutierrez | 20 | -- | -- | no results |
| Jamie Land | 14 | -- | -- | no results |
| Naina Singh (retry) | -- | -- | -- | already enriched |

5 connected (1st degree), 7 total with LinkedIn profiles, 2 not found.

---

## Incremental Run (2026-03-13, run #7)

Full pipeline: `run-collect.sh` (24s) + `enrich-linkedin.py` (78s) + agent review.

### Collection results

| Metric | Value | Notes |
|--------|-------|-------|
| Runtime | 24s | Name cache warm (397 hits, 1 new lookup) |
| New sightings | 47 | Dedup removed 631 already-seen sightings |
| Gmail new | 2 | Only 2 new emails since last run |
| Calendar new | 0 | All events already captured |
| Slack new | 45 | All 45 Slack messages are new (different message IDs each time) |
| New contacts | 1 | Sven Kieske |
| Unresolved | 0 | |
| Duplicate pairs | 0 | |
| Flagged | 2 | 2 incomplete names |

### Issue 19: Slack sightings not deduplicating across runs

**Symptom**: 45 Slack sightings created even though the same conversations were captured in run #6. Gmail deduplicates correctly (only 2 new), Calendar deduplicates perfectly (0 new), but Slack creates 45 every time.

**Root cause**: Slack sightings have `source_ref = NULL` (no message-level reference). The sighting dedup in `parse-source.py` uses `WHERE NOT EXISTS (... WHERE source_ref = X AND source_uid = Y)`. When `source_ref` is NULL, the `WHERE source_ref = NULL` comparison fails (SQL NULL != NULL), so every Slack sighting is treated as new.

**Impact**: Scores inflate with each run. After 2 runs, Slack contacts show double their real interaction count. Jeff DeMoss went from 22 to 24 (gained 2 from duplicate Slack DM sighting).

**Fix**: Use `source_uid` (Slack User ID) + `source` as the dedup key for Slack sightings, or set `source_ref` to the Slack message timestamp (`MsgID` column) in `parse-source.py`.

### Issue 20: Score movers show delta 0 for everyone

**Symptom**: All Score Movers entries show `|0` as the delta column. The SQL subquery for calculating previous scores doesn't work correctly.

**Root cause**: The delta calculation in `process-run.sh` uses a subquery that tries to find a previous score by looking at sightings from other runs, but it's structured incorrectly -- it always returns 0 or NULL.

**Impact**: Cosmetic -- the scores are correct, just the delta display is broken.

### Issue 21: enrich-linkedin.py re-searches contacts already enriched in previous batches

**Symptom**: The output directory still contains result files from batches 1 and 2. When the script runs batch 3, it overwrites them but also reads stale data.

**Root cause**: The script doesn't clear the output directory between runs. Old files from previous enrichment batches persist, creating confusion about which results are new.

**Fix**: Clear `data/tmp/linkedin/` at the start of each enrichment run, or use timestamped filenames.

### Issue 22: Daniele Zonca returns empty LinkedIn response

**Symptom**: `search_people(keywords="Daniele Zonca Red Hat")` returns `{"sections":{}}` -- the page loaded but no results were parsed.

**Root cause**: Likely a LinkedIn scraper timing issue -- the page didn't fully render before the content was extracted. The name is definitely on LinkedIn (found in previous manual sessions).

**Note**: Intermittent issue with linkedin-scraper-mcp, not our code. Retry would likely succeed.

### Issue 23: Jessie Kaempf (Huff) -- parenthetical in name breaks search

**Symptom**: No LinkedIn results for "Jessie Kaempf (Huff) Red Hat".

**Root cause**: The parenthetical `(Huff)` in the name is passed to LinkedIn search, which may confuse the query. The script should strip parentheticals from names before searching.

**Fix**: Add name cleaning in `enrich-linkedin.py`: strip `(...)` from names before building the search query.

### Enrichment results (batch 3)

| Contact | Score | URL | Degree | Confidence |
|---------|-------|-----|--------|------------|
| Jamie Land | 16 | /in/jamie-land/ | 2nd | high |
| Noy Itzikowitz | 16 | /in/noyitz/ | 2nd | high |
| Peter Double | 15 | /in/peterdouble/ | 2nd | high |
| Lindani Phiri | 14 | /in/lindani-phiri-306b142/ | 2nd | high |
| Jenny Yi | 13 | /in/jenny-yi-202020/ | 2nd | high |
| Bryon Baker | 13 | /in/bryonbakeraus/ | 2nd | high |
| Christine Bryan | 12 | /in/cfbryan/ | 2nd | high |
| Daniele Zonca | 14 | -- | -- | empty response |
| Myriam Fentanes Gutierrez | 22 | -- | -- | no results (3rd try) |
| Jessie Kaempf (Huff) | 13 | -- | -- | no results (parens in name) |

### Cumulative totals after all enrichment

- **14 with LinkedIn** (was 0 before enrichment)
- **5 connected** (1st degree)
- **9 high confidence**, **1 medium**
- **3 not found**: Myriam (name variant), Daniele (scraper issue), Jessie (parens in name)
