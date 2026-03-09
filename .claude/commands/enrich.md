Find LinkedIn profiles for contacts who don't have one yet. Process up to $ARGUMENTS people (default: 10).

## Instructions

You are enriching contacts in `./data/contacts.db` with LinkedIn profile URLs. Read `CLAUDE.md` for confidence scoring rules.

### Pre-flight

1. Check that `./data/contacts.db` exists. If not, tell the user to run `/collect` first.
2. Record the run start time.
3. **Check linkedin_connections table first** -- mark already-connected people before doing any searches:
   ```sql
   -- Match by email
   UPDATE people SET
     linkedin_url = lc.linkedin_url,
     status = 'connected',
     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   FROM linkedin_connections lc
   WHERE people.email = lc.email
     AND people.linkedin_url IS NULL
     AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '';

   -- Match by name (first + last)
   UPDATE people SET
     linkedin_url = lc.linkedin_url,
     status = 'connected',
     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   FROM linkedin_connections lc
   WHERE people.linkedin_url IS NULL
     AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != ''
     AND LOWER(people.name) = LOWER(lc.first_name || ' ' || lc.last_name)
     AND people.status != 'connected';
   ```
   Report how many were matched from the connections table.
4. Query for people who still need enrichment:
   ```sql
   SELECT id, name, company, company_domain, email
   FROM people
   WHERE linkedin_url IS NULL AND status != 'connected'
   ORDER BY interaction_score DESC
   LIMIT <batch_size>;
   ```
   This prioritizes high-interaction contacts first and skips already-connected ones.

### For each person

1. **Construct a search query.** Use the person's name and company to build:
   ```
   site:linkedin.com/in "{name}" "{company}"
   ```
   If company is unknown, use just:
   ```
   site:linkedin.com/in "{name}" "{company_domain}"
   ```
   If both are unknown, use:
   ```
   site:linkedin.com/in "{name}"
   ```

2. **Search.** Use whatever web search tool is available (web_search, Brave MCP, etc.) to run the query.

3. **Log the search attempt.** Immediately after receiving results, INSERT into `linkedin_searches`:
   ```sql
   INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
   VALUES (<person_id>, <run_id>, '<query>', '<candidates_json>', NULL, NULL, NULL);
   ```
   - `candidates`: JSON array of top results, e.g. `[{"url":"https://linkedin.com/in/janedoe","name":"Jane Doe","headline":"Engineer at Acme","company":"Acme"}]`
   - `chosen_url`, `confidence`, `notes` are filled in after evaluation (steps 4-6)

4. **Evaluate the top result(s).**
   - Does the LinkedIn profile name match the person's name? (Allow for nickname variations: "Mike" = "Michael", "Jon" = "Jonathan", etc.)
   - Does the company/headline on the profile match what we know?
   - Is this an `/in/` profile URL (not a company page or post)?

5. **Assign confidence:**
   - **high**: Name clearly matches AND company/headline matches.
   - **medium**: Name matches but company doesn't match or is missing from the profile.
   - **low**: Ambiguous -- multiple candidates, very common name, or weak match.

6. **Check if already connected.** After finding a URL, check against the local connections DB:
   ```sql
   SELECT 1 FROM linkedin_connections WHERE linkedin_url = '<found_url>';
   ```
   If matched, set `status = 'connected'` in addition to setting the URL.

7. **Check connection degree via LinkedIn MCP.** If the `linkedin` MCP server is available, call `get_person_profile` with the found URL. Parse the response for connection degree (look for "1st", "1st degree", "connected", or equivalent):
   - If 1st-degree: set `status = 'connected'` in the UPDATE below, and append `"Connection degree: 1st"` to the `linkedin_searches.notes`.
   - If 2nd/3rd-degree or not connected: leave `status` unchanged, append `"Connection degree: 2nd"` (or 3rd/none) to notes.
   - If the LinkedIn MCP is unavailable or rate-limited: skip silently, append `"Connection degree: not checked (MCP unavailable)"` to notes.
   - Rate limit: max **5** profile lookups per enrich run, max 10 per day. Add 3-5 second pauses between calls. Stop immediately on any error.

8. **Update the database** -- both the `linkedin_searches` row and the person:
   ```sql
   UPDATE linkedin_searches SET chosen_url = '<url>', confidence = '<confidence>',
       notes = '<agent reasoning for choice>'
   WHERE id = <search_id>;

   UPDATE people
   SET linkedin_url = '<url>',
       linkedin_confidence = '<confidence>',
       status = CASE WHEN '<is_connected>' = 'yes' THEN 'connected' ELSE status END,
       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   WHERE id = <person_id>;
   ```

9. **Skip conditions** -- do NOT search for a person if:
   - They already have a `linkedin_url` set.
   - Their `status` is already `'connected'`.
   - Their name is clearly not a real person (e.g., "Billing Department", "IT Support").
   - You've already found that the name is too generic to get a reliable match.
   - For skipped people, still INSERT a `linkedin_searches` row with `chosen_url = NULL` and `notes` explaining why (e.g., "Skipped: name too generic").

### Finalize

1. Record the run in `runs` table with source = 'enrich'.
2. Print a summary:
   ```
   Enrichment complete
   ───────────────────
   Searched:  X people
   Found:     X LinkedIn profiles
     High:    X
     Medium:  X
     Low:     X
   Connected: X confirmed 1st-degree (via MCP check)
   Skipped:   X (too generic / not a person)
   Remaining: X people still without LinkedIn
   ```

### Important

- Be conservative with confidence. When in doubt, mark as "low" rather than assigning the wrong profile.
- If a search returns no results, leave `linkedin_url` as NULL. Don't force a match.
- Respect web search rate limits. If you hit a limit, stop and report how many were processed.
- The LinkedIn URL should be the canonical profile URL, e.g. `https://www.linkedin.com/in/janedoe/`
- The `linkedin` MCP server (`linkedin-scraper-mcp`) can be used for live profile lookups via `get_person_profile` and `search_people`, but use it sparingly to avoid LinkedIn rate limits.
- Always check `linkedin_connections` table first before doing web searches -- it's free and instant.
