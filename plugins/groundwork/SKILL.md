# Groundwork — OpenClaw Plugin Skill

Groundwork is a personal contact-intelligence pipeline. It collects contacts
from Gmail, Calendar, and Slack, deduplicates them, enriches them with
LinkedIn profiles, and stores everything in a local SQLite database.

This skill is injected into the OpenClaw system prompt when the `groundwork`
plugin is loaded. It teaches you how to use the `groundwork_status` tool and
the `/groundwork` command, and how to answer follow-up questions via `exec`.

---

## Available Plugin Tools

### `groundwork_status`
The primary data-access tool. Call it in these modes:

**Connectivity check (run first if unsure the DB is configured):**
```
groundwork_status({ health_check: true })
```
Returns a status string. If it reports an error, stop and relay the error to
the user — do not call any other Groundwork tools until the path is resolved.

**Latest run digest (default, JSON):**
```
groundwork_status({ format: "json" })
```
Returns:
```json
{
  "run": { "run_id": 42, "run_date": "2026-04-16", "sightings": 87 },
  "new_contacts": 5,
  "notable_contacts": [
    { "id": 3, "name": "Alice Chen", "score": 45, "channel_diversity": 2,
      "sources": "gmail,calendar", "linkedin_url": null }
  ],
  "enrichment_candidates": [
    { "id": 7, "name": "Bob Torres", "score": 32, "company": "Acme Corp" }
  ],
  "flags": {
    "unresolved_sightings": 0,
    "duplicate_pairs": 2,
    "incomplete_names": 1,
    "total": 3
  }
}
```

**Human-readable message digest:**
```
groundwork_status({ format: "message" })
```
Returns the compact text digest ready to relay to the user.

**Override score threshold:**
```
groundwork_status({ format: "json", min_score: 10 })
```

**Specific run:**
```
groundwork_status({ format: "json", run_id: 38 })
```

---

## When to Call `groundwork_status`

- User asks "what's new", "show contacts", "groundwork status", or similar
- After the `/groundwork collect` command completes
- Before answering any question about recent contacts or run results

Always start with `{ health_check: true }` if:
- This is the first Groundwork call in the conversation, **and**
- You haven't already confirmed the DB is reachable in this session

---

## Available Slash Command

The `/groundwork` command is registered on all connected messaging channels.

| Invocation | Action |
|---|---|
| `/groundwork` | Show latest run digest |
| `/groundwork check` | Run connectivity health-check |
| `/groundwork collect` | Run the full collect pipeline and return digest |
| `/groundwork enrich` | Run LinkedIn enrichment (batch of 5) |

---

## Follow-up Queries via `exec`

The plugin config provides `project_path` (the Groundwork directory) and
`db_path`. Use them for direct `sqlite3` follow-ups. The `exec` tool must be
enabled in your tool profile.

### Show a person
```
exec: sqlite3 "<db_path>" \
  "SELECT id, name, email, company, interaction_score, channel_diversity,
          sources, linkedin_url, status, last_seen
   FROM people WHERE LOWER(name) LIKE LOWER('%<name>%') LIMIT 5;"
```

### Top enrichment candidates
```
exec: sqlite3 "<db_path>" \
  "SELECT id, name, email, company, interaction_score, channel_diversity
   FROM people
   WHERE linkedin_url IS NULL AND status NOT IN ('ignored','wrong_match')
   ORDER BY interaction_score DESC LIMIT 10;"
```

### Contacts from a company
```
exec: sqlite3 "<db_path>" \
  "SELECT id, name, email, interaction_score, status
   FROM people WHERE LOWER(company) LIKE LOWER('%<company>%') AND status != 'ignored'
   ORDER BY interaction_score DESC;"
```

### Duplicate pairs
```
exec: sqlite3 "<db_path>" \
  "SELECT a.id, a.name, a.email, b.id, b.name, b.email
   FROM people a JOIN people b ON a.id < b.id
   WHERE LOWER(a.name) = LOWER(b.name)
     AND a.company_domain = b.company_domain
     AND a.company_domain IS NOT NULL
     AND a.status != 'ignored' AND b.status != 'ignored'
   LIMIT 10;"
```

---

## Conversational Actions

### Enrich LinkedIn (batch)
```
exec: cd "<project_path>" && python3 scripts/enrich-linkedin.py --batch-size 5
```
After running, review `data/tmp/linkedin/*.json` files and update the database.
See the Groundwork LinkedIn enrichment skill for the evaluation protocol.

### Ignore a contact
```
exec: sqlite3 "<db_path>" \
  "UPDATE people SET status='ignored',
     updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
   WHERE id = <ID>;"
```
Confirm the change with the user before executing.

### Merge duplicates
```
exec: cd "<project_path>" && ./scripts/merge-people.sh <keep_id> <merge_id>
```

### Full status report
```
exec: cd "<project_path>" && ./scripts/status.sh
```

---

## Score Reference

| Score | Meaning |
|---|---|
| 0–5 | Weak signal (large meetings, mailing lists) |
| 6–14 | At least one direct interaction |
| **15+** | Meaningful direct contact — appears in digest |
| 50+ | Ongoing multi-channel relationship |

`channel_diversity` counts distinct interaction types (email_sent, email_received,
meeting, slack_dm). A value of 2+ means multi-channel — a stronger signal than
a high score with only one channel.

---

## Constraints

- Never auto-send LinkedIn connection requests
- Never display or store email body content
- Always confirm before any UPDATE or DELETE
- Present enrichment candidates; let the user decide who to connect with
