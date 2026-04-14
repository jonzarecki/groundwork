---
name: linkedin-enrich
description: Find LinkedIn profiles for contacts using a multi-strategy search with traceability. Derives company from email domain, tries web search API first, falls back to Google browser search. Logs every attempt in linkedin_searches. Use when enriching contacts, finding LinkedIn profiles, or when the user says "enrich".
---

# LinkedIn Enrichment Strategy

## Pre-flight

1. Read `.env` for config: `source .env 2>/dev/null`
2. Check `linkedin_connections` table first -- match by email or name. Free, instant, always do this before any web search.
3. Query candidates: `SELECT id, name, company, company_domain, email FROM people WHERE linkedin_url IS NULL AND status != 'connected' AND name LIKE '% %' ORDER BY interaction_score DESC LIMIT <batch_size>`
4. Skip contacts without full names (single-word auto-derived names are too ambiguous for LinkedIn search).

## For each contact

### Step 1: Derive company name from email domain

Map `company_domain` to a human-readable company name for the search query:
- `redhat.com` -> `Red Hat`
- `il.ibm.com` -> `IBM`
- `google.com` -> `Google`
- Generic: strip TLD, capitalize (e.g., `acme.co` -> `Acme`)

### Step 2: Search via LinkedIn MCP `search_people`

Use the `search_people` tool from the `linkedin` MCP server. This is the primary and preferred strategy -- it returns real LinkedIn URLs (no slug guessing), connection degree, headline, and mutual connections.

```
Call: search_people(keywords="{name} {company}")
```

Parse the response. The profile URL is in `references.search_results[].url`, NOT in the text:
- Profile URL: extract from `references` -> items with `kind: "person"` -> `url` field (e.g., `/in/jenny-yi-202020/`). This is the ONLY reliable source of the slug.
- Connection degree: parse from `sections.search_results` text (`1st`, `2nd`, `3rd+`)
- Headline: from sections text (verify it matches the expected company/role)
- Mutual connections count: from sections text (higher = more confident match)
- If 1st degree: also set `status = 'connected'` on the person

**If `search_people` returns no results or ambiguous results**, try broader:
```
Call: search_people(keywords="{name}", location="{location if known}")
```

**Fallback (last resort, Cursor only)**: Google browser search. Navigate to `https://www.google.com/search?q=linkedin.com+%22{name}%22+%22{company}%22&hl=en`, click the LinkedIn result to extract the real URL from the redirect. This does NOT work headless or in Claude Code.

**Never guess LinkedIn slugs.** Only use URLs returned directly by `search_people` or extracted from verified sources.

### Step 3: Log the search (always, even failures)

Use the batch script — **never write raw sqlite3 SQL per person**. Collect all results for the session, then save in one call:

```bash
python3 scripts/save-linkedin-batch.py --rows '[
  {"id": 123, "url": "https://www.linkedin.com/in/slug/", "confidence": "high",
   "query": "Name Company", "notes": "reason", "candidates": [...]},
  {"id": 456, "url": null, "confidence": null,
   "query": "Name Company", "notes": "No match found"}
]'
```

For a single one-off save (e.g. user asks to look up one person):

```bash
./scripts/save-linkedin.sh --id 123 --url https://www.linkedin.com/in/slug/ \
    --confidence high --notes "reason"
```

Fields:
- `id`: `people.id`
- `url`: full LinkedIn URL, or `null` for no match
- `confidence`: `high` / `medium` / `low` / `null`
- `query`: search string used (auto-derived from name+company if omitted)
- `notes`: agent reasoning for the choice or why it was skipped
- `candidates`: JSON array of all results considered — `[{"name":"...", "headline":"...", "url":"...","degree":"..."}]` (optional, defaults to `[]`)

### Step 4: Fast path — skip WebFetch when confidence is already high

Check all of these before calling WebFetch. If **all** are true, go directly to Step 6 (save):

1. Headline explicitly contains the expected company name (e.g. "Red Hat")
2. Connection degree is 1st or 2nd
3. At least 1 mutual connection name matches a person in our DB:
   ```bash
   sqlite3 data/contacts.db "SELECT name FROM people WHERE name IN ('<mutual1>','<mutual2>') AND status='connected';"
   ```
4. Only 1 plausible result returned (or the top result is clearly the target)

**Require WebFetch** when any of these apply:
- Headline does not mention the expected company (medium confidence)
- Multiple similar-looking results need disambiguation
- Degree is 3rd+ with no known mutuals
- You are uncertain for any reason

This skips WebFetch for ~90% of Red Hat contacts (single result + "Red Hat" in headline + Red Hat mutual) and saves significant tokens.

### Step 5: Validate the URL resolves to the right person (WebFetch, if fast path not taken)

Before saving, verify the URL came from a real source:

- The `/in/slug/` MUST appear in the `search_people` response `references` field, NOT be constructed from the person's name
- Red flag: if `slug == lowercase(name).replace(' ', '-')` and there's no `url` field in the MCP response, the URL was guessed -- do NOT use it
- Numeric suffixes (`-9867807`, `-0783254`, `-bab8b3360`) and username-style slugs (`abraren`, `cecetn`, `noyitz`) indicate real URLs
- Name-pattern slugs (`adam-bellusci`, `alex-corvin`, `rob-greenberg`) are almost always wrong

If the URL looks guessed, set `chosen_url = NULL` and log `"No real URL in search results, skipping name-guessed slug"` in notes.

After finding a URL, verify it points to the correct person by fetching the public profile (unless fast path was taken):

1. Call `WebFetch` on the LinkedIn URL (e.g., `https://www.linkedin.com/in/abraren/`)
2. Pipe the result to the validator:
   ```bash
   echo "$WEBFETCH_CONTENT" | python3 scripts/validate-linkedin-url.py \
       --name "Andy Braren" --company-domain "redhat.com"
   ```
3. Check the result:
   - `status=valid` → proceed to update
   - `status=mismatch` → WRONG PERSON, do NOT save the URL. Log in notes: `"WebFetch validation failed: expected X, found Y"`
   - `status=not_found` → URL doesn't resolve, don't save
   - `status=inconclusive` → save with `medium` confidence, note `"WebFetch validation inconclusive"`

This catches cases where the `search_people` URL belongs to a different person with a similar name (e.g., `/in/ryan-cook/` pointing to a Ryan Cook at Google instead of Red Hat).

WebFetch is available in both Cursor and Claude Code.

### Step 6: Update the person (only if URL is validated)

Add the result to the session batch (see Step 3). Do not write raw SQL per person — use `save-linkedin-batch.py` at the end of the session.

After saving, also check connection status against the local CSV import:
```sql
SELECT 1 FROM linkedin_connections WHERE linkedin_url = ?;
```
If matched, set `status = 'connected'`.

### Step 7: Check connection degree (live)

After setting `linkedin_url`, call `get_person_profile(url)` via the `linkedin` MCP server to check the actual connection degree:

- Parse the response for connection degree: look for "1st", "2nd", "3rd", "connected", or equivalent field
- If **1st-degree**: `UPDATE people SET status = 'connected' WHERE id = ?`
- Append the result to `linkedin_searches.notes` for this person, e.g., `"Connection degree: 1st"`
- If 2nd/3rd/none: leave status unchanged, still log the degree in notes
- If the MCP is unavailable or returns an error: skip silently, note `"Connection degree: not checked"`

**Rate limiting**: max 10 `get_person_profile` calls per enrich run. LinkedIn throttles aggressive profile lookups. If you hit a rate limit, stop checking and note it in the run log.

This step catches connections made since the last CSV export -- the CSV is a snapshot, but the MCP check is live.

## Confidence rules

- **high**: Name AND company/headline both match clearly
- **medium**: Name matches but company is different, missing, or ambiguous
- **low**: Multiple candidates, very common name, or weak match

## Importing LinkedIn connections (bulk)

When the user says "import linkedin connections" or wants to bulk-match their network:

1. Open `https://www.linkedin.com/mypreferences/d/download-my-data` in the browser panel
2. Guide the user: select **"Download larger data archive"** (the full archive option at the top — LinkedIn removed the individual "Connections" checkbox). Click **Request archive**.
3. LinkedIn emails the archive link ~15 minutes after the request. The ZIP file is named like `Basic_LinkedInDataExport_MM-DD-YYYY.zip.zip` and lands in `~/Downloads/`.
4. Extract `Connections.csv` from the ZIP: `unzip -o ~/Downloads/Basic_LinkedInDataExport_*.zip.zip -d /tmp/linkedin-export/`
5. Run: `./scripts/import-connections.sh /tmp/linkedin-export/Connections.csv`
6. Cross-reference: match by email, then by name, set `status = 'connected'`

Alternatively, the user can use the **Import LinkedIn** button in the viewer (`viewer/index.html`) which has a 3-step walkthrough with drag-and-drop CSV import.

## LinkedIn MCP usage policy

The LinkedIn MCP runs via `uvx linkedin-scraper-mcp` (stickerdaniel/linkedin-mcp-server, Apache-2.0, ~1K stars). It uses browser automation to access LinkedIn and violates LinkedIn ToS. Use conservatively to minimize account risk.

**If a project-configured LinkedIn MCP is already available** (e.g. the `linkedin` server in `.mcp.json` or `.cursor/mcp.json`), use it directly via its tools — no `li_at` cookie or `setup-auth.py linkedin` step is needed. The `search_people` and `get_person_profile` calls in the steps below work the same regardless of which MCP instance provides them. The `linkedin-scraper-mcp` package (`uvx linkedin-scraper-mcp`) is the recommended MCP server for this project if one isn't already configured — set it up with:

```bash
uvx linkedin-scraper-mcp --login --no-headless
```

Then add it to `.mcp.json`:
```json
"linkedin": {
  "command": "uvx",
  "args": ["linkedin-scraper-mcp"]
}
```

This is preferred over the `li_at` cookie approach when the MCP stack is already running.

v4.4.1+ includes our contributed fix (PR #225) for `search_people` returning empty results ~40% of the time. If you hit empty results on an older version, upgrade with `uvx linkedin-scraper-mcp --upgrade`.

**Hard limits per session:**
- Max **10 `get_person_profile` calls** per enrich run
- Max **50 profile lookups per day** across all runs
- **Never** call in a tight loop -- add 3-5 second pauses between calls
- If you get a rate limit or error, **stop immediately** and note it in the run log

**When to use:**
- Connection degree checks (Step 5) after finding a LinkedIn URL
- Verifying a profile match when confidence is medium/low
- **Never** for bulk scraping or discovery -- use web search for finding profiles

**When NOT to use:**
- Large batch enrichment (use web search + browser instead)
- If the user hasn't completed `--login` setup (check `~/.linkedin-mcp/profile/` exists)
- If the MCP server isn't connected (check Cursor MCP panel)

**Auth setup:**
```bash
uvx linkedin-scraper-mcp --login --no-headless
```
Creates persistent browser profile at `~/.linkedin-mcp/profile/`. Session may expire -- re-run `--login` if `--status` shows expired.

## Common gotchas

- **If `search_people` returns empty sections**, the content hydration may have timed out despite the fix. Fall back to `WebSearch` for `linkedin.com/in "{name}" "{company}"`, then validate the URL with `WebFetch`. This is a secondary path -- the patched MCP should work for the vast majority of searches.
- **NEVER guess LinkedIn slugs from names.** Slugs often have numeric suffixes (`daniele-zonca-9867807`, `kezia-cook-bab8b3360`) that are impossible to guess. Always extract the actual URL from Google search results.
- **To extract the URL from Google browser search**: click the LinkedIn result link, then read the `sessionRedirect` parameter from the authwall URL. The redirect contains the real profile URL (e.g., `https://it.linkedin.com/in/daniele-zonca-9867807`).
- **Always verify with LinkedIn MCP after setting a URL.** Call `get_person_profile` and check the headline/company matches. If the response shows a different person (wrong company, 3rd-degree with no Red Hat connection), the URL is wrong -- remove it.
- LinkedIn names may differ from email names: "Jamie Land" -> "James Land" on LinkedIn
- The `site:linkedin.com/in` search often fails for Red Hat employees via web search APIs. Always have Tier 2 (browser search) ready.
- External contacts (IBM, etc.) may not appear in Slack -- rely on calendar/email data for company context
- Jenny Yi / Jenny Yang / Jennifer Yi -- common names need company constraint to disambiguate
