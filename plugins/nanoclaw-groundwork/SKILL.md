# Groundwork — NanoClaw Skill

Groundwork is a personal contact-intelligence pipeline that collects contacts
from Gmail, Calendar, and Slack, deduplicates them, enriches them with LinkedIn
profiles, and stores everything in a local SQLite database.

This skill lets you interact with a running Groundwork instance directly from
NanoClaw via shell exec. No HTTP server is required — all operations read and
write the SQLite database on disk.

---

## Setup and Onboarding

**Before answering any Groundwork-related question, run the health-check below.**
Run it automatically the first time the user mentions "groundwork", and re-run
it any time you get a file-not-found or sqlite error (the mount path may have
drifted).

### Health-check sequence

Run these three checks in order. Stop and report clearly if any fails — do not
continue to other commands until the path issue is resolved.

**Step 1 — Locate the database:**
```
exec: test -f GROUNDWORK_PATH/data/contacts.db && echo "found" || echo "not found"
```
- `found` → proceed to step 2
- `not found` → tell the user:
  > "I couldn't find the Groundwork database at `GROUNDWORK_PATH/data/contacts.db`.
  > Please confirm:
  > 1. The Groundwork project directory is mounted at `GROUNDWORK_PATH` in my
  >    container config (check with `docker inspect <container> | grep Mounts`)
  > 2. Or tell me the correct path and I'll use that instead."
  >
  > Do not run any further Groundwork commands until the user confirms the path.

**Step 2 — Verify the schema and row count:**
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db "SELECT COUNT(*) FROM people;"
```
- Returns a number → note the count, proceed to step 3
- Error "no such table" → warn:
  > "The database exists but is uninitialised. Run `./scripts/setup.sh` in the
  > Groundwork directory to create the schema."
- Returns `0` → note:
  > "The database is empty. Run `./scripts/run-collect.sh` to collect your first
  > contacts."

**Step 3 — Confirm scripts are present:**
```
exec: test -f GROUNDWORK_PATH/scripts/notify-run.py && echo "found" || echo "not found"
```
- `found` → health-check passed
- `not found` → warn:
  > "The Groundwork scripts directory wasn't found at the expected path. Check
  > the mount configuration."

**Step 4 — Report success:**
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT COUNT(*) FROM people; \
   SELECT finished_at FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1;"
```
Reply: "Groundwork is connected. Found N contacts at `GROUNDWORK_PATH/data/contacts.db`.
Latest run: <date>. Ready."

---

## Path Configuration

The Groundwork project directory is mounted into the container at:

```
GROUNDWORK_PATH=/groundwork
```

All commands below use this prefix. If the user specifies a different path,
substitute it and remember it for the rest of the conversation.

The database is at: `GROUNDWORK_PATH/data/contacts.db`

---

## Getting the Post-Run Digest

After a collect run, or when the user asks "what's new" / "show groundwork":

```
exec: python3 GROUNDWORK_PATH/scripts/notify-run.py \
        --db GROUNDWORK_PATH/data/contacts.db \
        --format message
```

This prints notable new contacts (score ≥ 15) and enrichment candidates.
If the output is empty, there is nothing actionable — report that briefly.

For raw JSON (useful for programmatic follow-ups):
```
exec: python3 GROUNDWORK_PATH/scripts/notify-run.py \
        --db GROUNDWORK_PATH/data/contacts.db \
        --format json
```

To lower the score threshold (e.g. show contacts scoring ≥ 10):
```
exec: python3 GROUNDWORK_PATH/scripts/notify-run.py \
        --db GROUNDWORK_PATH/data/contacts.db \
        --min-score 10 \
        --format message
```

---

## Running the Collect Pipeline

When the user says "collect", "run groundwork", or "refresh contacts":
```
exec: cd GROUNDWORK_PATH && ./scripts/run-collect.sh
```
Optional days argument (default 7):
```
exec: cd GROUNDWORK_PATH && ./scripts/run-collect.sh 14
```
Wait for it to complete, then show the digest section from the output.

---

## Querying Contacts

Use `sqlite3` for natural-language follow-up queries. Always use
`GROUNDWORK_PATH/data/contacts.db` as the DB path.

### Show a specific person
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, name, email, company, interaction_score, channel_diversity,
          sources, linkedin_url, status, last_seen
   FROM people
   WHERE LOWER(name) LIKE LOWER('%Alice%')
   LIMIT 5;"
```

### Top contacts by score (no LinkedIn yet)
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, name, email, company, interaction_score, channel_diversity, sources
   FROM people
   WHERE linkedin_url IS NULL AND status NOT IN ('ignored','wrong_match')
   ORDER BY interaction_score DESC
   LIMIT 10;"
```

### Contacts from a specific company
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, name, email, interaction_score, status
   FROM people
   WHERE LOWER(company) LIKE LOWER('%Acme%') AND status != 'ignored'
   ORDER BY interaction_score DESC;"
```

### Contacts seen recently
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, name, email, interaction_score, last_seen, sources
   FROM people
   WHERE last_seen >= date('now','-30 days') AND status != 'ignored'
   ORDER BY last_seen DESC
   LIMIT 20;"
```

### Recent runs
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, started_at, finished_at, source, contacts_found,
          contacts_new, contacts_updated
   FROM runs ORDER BY id DESC LIMIT 10;"
```

---

## Conversational Actions

### "Enrich" — find LinkedIn profiles
```
exec: cd GROUNDWORK_PATH && python3 scripts/enrich-linkedin.py --batch-size 5
```
After the script finishes, it saves raw results to `data/tmp/linkedin/*.json`.
You then review each file, evaluate the match (name + company + degree), and
update the database. See `.cursor/skills/linkedin-enrich/SKILL.md` for the
full evaluation protocol.

### "Ignore" a contact
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "UPDATE people SET status='ignored', updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
   WHERE id = <ID>;"
```
Confirm: "Ignored <name> (id <ID>)."

### "Review" / show duplicates
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT a.id, a.name, a.email, a.interaction_score,
          b.id, b.name, b.email, b.interaction_score
   FROM people a JOIN people b ON a.id < b.id
   WHERE LOWER(a.name) = LOWER(b.name)
     AND a.company_domain = b.company_domain
     AND a.company_domain IS NOT NULL
     AND a.status != 'ignored' AND b.status != 'ignored'
   ORDER BY a.interaction_score DESC
   LIMIT 10;"
```
Present each pair. If the user confirms they are the same person:
```
exec: cd GROUNDWORK_PATH && ./scripts/merge-people.sh <keep_id> <merge_id>
```

### "Merge" two contacts
```
exec: cd GROUNDWORK_PATH && ./scripts/merge-people.sh <keep_id> <merge_id>
```

### "Status" — overall database stats
```
exec: cd GROUNDWORK_PATH && ./scripts/status.sh
```

### "Who should I connect with?"
Query people with high scores and no LinkedIn connection yet:
```
exec: sqlite3 GROUNDWORK_PATH/data/contacts.db \
  "SELECT id, name, email, company, interaction_score, channel_diversity, sources
   FROM people
   WHERE status NOT IN ('connected','ignored','wrong_match')
     AND linkedin_url IS NULL
     AND interaction_score >= 15
   ORDER BY channel_diversity DESC, interaction_score DESC
   LIMIT 10;"
```
Prioritise by `channel_diversity` first (multi-channel = stronger signal), then `interaction_score`.

---

## Key Schema Reference

### `people` table (the main output)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER | Primary key |
| `name` | TEXT | Full name |
| `email` | TEXT | Unique; may be NULL for Slack-only contacts |
| `company` | TEXT | |
| `company_domain` | TEXT | e.g. `acme.com` |
| `linkedin_url` | TEXT | NULL until enriched |
| `linkedin_confidence` | TEXT | `high` / `medium` / `low` |
| `interaction_score` | INTEGER | Higher = more direct contact |
| `channel_diversity` | INTEGER | Count of distinct interaction types |
| `first_seen` | TEXT | ISO timestamp |
| `last_seen` | TEXT | ISO timestamp |
| `sources` | TEXT | Comma-separated: `gmail`, `calendar`, `slack` |
| `status` | TEXT | `new` / `reviewed` / `connected` / `ignored` / `wrong_match` |

### `runs` table
| Column | Notes |
|--------|-------|
| `id` | Run ID |
| `started_at` / `finished_at` | ISO timestamps |
| `source` | `all` for full collect runs, `enrich` for LinkedIn runs |
| `contacts_found` | Sightings in this run |
| `contacts_new` | Brand-new people |
| `contacts_updated` | People seen again |

### Score tiers
- **0–5**: weak signal only (mailing lists, large meetings)
- **6–14**: at least one direct interaction
- **15+**: meaningful direct contact — the threshold for suggestions
- **50+**: ongoing multi-channel relationship

---

## Important Constraints

- **Never auto-send LinkedIn connection requests** — only surface candidates for the user to act on
- **Never store or display email body content** — context fields contain subjects/titles only
- **Confirm before any UPDATE/DELETE** — tell the user what you're about to change and wait for confirmation
- **DB writes are immediate** — SQLite has no rollback once committed; use the merge_log for auditing merges
