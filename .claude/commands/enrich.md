Find LinkedIn profiles for contacts who don't have one yet. Process up to $ARGUMENTS people (default: LC_ENRICH_BATCH_SIZE or 10).

## Step 1: Run the search script

```bash
python3 scripts/enrich-linkedin.py --batch-size ${ARGUMENTS:-10}
```

This connects to the LinkedIn MCP, searches for each unenriched contact, and saves raw responses to `data/tmp/linkedin/`. The script handles rate limiting (3-5s pauses).

If the script reports "Could not connect to LinkedIn MCP", tell the user:
```
uvx linkedin-scraper-mcp --login --no-headless
```

## Step 2: Review results (agent judgment)

For each file in `data/tmp/linkedin/`:

1. Read the JSON file (contains person info + raw LinkedIn search response)
2. Parse the response for candidates:
   - Profile URL: extract from `references` field (e.g., `/in/jenny-yi-202020/`)
   - Connection degree: look for `1st`, `2nd`, `3rd+` in sections text
   - Headline/company: verify it matches the expected person
3. Evaluate confidence:
   - **high**: Name AND company/headline both match, URL from references
   - **medium**: Name matches but company differs or missing
   - **low**: Ambiguous, multiple candidates, common name
4. **NEVER construct a LinkedIn URL from a person's name.** Only use URLs from the `references` field.

## Step 3: Update database

For each confident match:
```sql
UPDATE people SET
  linkedin_url = '<url>',
  linkedin_confidence = '<confidence>',
  status = CASE WHEN '<degree>' = '1st' THEN 'connected' ELSE status END,
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id = <person_id>;
```

Always log the search:
```sql
INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
VALUES (<person_id>, <run_id>, '<query>', '<candidates_json>', '<url or NULL>', '<confidence>', '<reasoning>');
```

## Step 4: Summary

Print a summary:
```
Enrichment complete
  Searched:  X people
  Found:     X LinkedIn profiles (High: X, Medium: X, Low: X)
  Connected: X confirmed 1st-degree
  Skipped:   X (no match or ambiguous)
  Remaining: X people still without LinkedIn
```

## Important

- NEVER guess LinkedIn slugs from names. Only use URLs from MCP `references` field.
- Real slugs have numeric suffixes (`-9867807`) or username styles (`abraren`). Name-pattern slugs (`adam-bellusci`) are almost always wrong.
- Always check `linkedin_connections` table first (done automatically by `resolve-sightings.sql`).
- Rate limits: max `LC_ENRICH_BATCH_SIZE` per run. Stop on errors.
