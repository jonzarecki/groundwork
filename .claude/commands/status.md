Show a summary of the contacts database.

## Instructions

Query `./data/contacts.db` and print a status report. If the database doesn't exist, tell the user to run `/collect` first.

Run these queries and format the output:

```sql
-- Total contacts
SELECT COUNT(*) FROM people;

-- By status
SELECT status, COUNT(*) FROM people GROUP BY status;

-- With LinkedIn
SELECT
  COUNT(*) FILTER (WHERE linkedin_url IS NOT NULL) as with_linkedin,
  COUNT(*) FILTER (WHERE linkedin_url IS NULL) as without_linkedin
FROM people;

-- LinkedIn confidence breakdown
SELECT linkedin_confidence, COUNT(*)
FROM people
WHERE linkedin_url IS NOT NULL
GROUP BY linkedin_confidence;

-- By source
SELECT sources, COUNT(*) FROM people GROUP BY sources;

-- New this week
SELECT COUNT(*) FROM people
WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days');

-- Top 10 by score (without LinkedIn)
SELECT name, company, interaction_score, sources, last_seen
FROM people
WHERE linkedin_url IS NULL AND status = 'new'
ORDER BY interaction_score DESC
LIMIT 10;

-- Recent runs
SELECT id, started_at, source, contacts_found, contacts_new, contacts_updated
FROM runs
ORDER BY started_at DESC
LIMIT 5;
```

Format the output as:

```
Linked Collector Status
═══════════════════════

Contacts: X total
  New:       X
  Reviewed:  X
  Connected: X
  Ignored:   X

LinkedIn: X with profile, X without
  High:    X
  Medium:  X
  Low:     X

Sources:
  gmail:           X
  calendar:        X
  slack:           X
  multiple:        X

New this week: X

Top unlinked contacts:
  Score | Name              | Company        | Sources  | Last seen
  ──────┼───────────────────┼────────────────┼──────────┼──────────
  12    | Jane Smith        | Anthropic      | gmail,cal| 2 days ago
  ...

Recent runs:
  #1  2026-03-05  all     found:45  new:12  updated:33
  ...
```
