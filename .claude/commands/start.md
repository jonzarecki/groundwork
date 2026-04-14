Your all-in-one daily command. Collects new contacts, enriches LinkedIn profiles, and opens the viewer. Run this after onboarding and any time you want to refresh your contacts.

## What this does

1. **Collect** — pulls new contacts from Gmail, Calendar, and Slack for the last `$ARGUMENTS` days (default: `LC_COLLECT_DAYS` or 7)
2. **Enrich** — finds LinkedIn profiles for unenriched contacts (up to `LC_ENRICH_BATCH_SIZE`)
3. **View** — launches the viewer at http://localhost:8080/viewer/index.html

## Step 1: Collect

```bash
./scripts/run-collect.sh ${ARGUMENTS:-}
```

Show the full output to the user. Note the number of new contacts and any flagged items.

## Step 2: Enrich LinkedIn

```bash
python3 scripts/enrich-linkedin.py --batch-size ${LC_ENRICH_BATCH_SIZE:-10}
```

Review each file in `data/tmp/linkedin/` and update the database as described in `/enrich`. If LinkedIn credentials are not set up (`data/.credentials/linkedin.json` missing), skip this step and tell the user they can set it up with `python3 scripts/setup-auth.py linkedin`.

## Step 3: Handle flagged items (if any)

If the collect report showed `Flagged for review > 0`, present those items for review now (B4 fuzzy candidates, ambiguous duplicates). Use `./scripts/merge-people.sh` for merges.

## Step 4: Launch viewer

Check if the server is already running:

```bash
lsof -i :8080 | grep LISTEN
```

If not running, start it:

```bash
python3 scripts/server.py &
```

Tell the user:

> "✓ Done! Your contacts viewer is running at http://localhost:8080/viewer/index.html"

## Summary to show the user

After completing all steps, print a clean summary:

```
=== Groundwork — Run complete ===

Contacts:     <TOTAL> total (<NEW_THIS_RUN> new this run)
LinkedIn:     <WITH_LINKEDIN> profiles found (<CONNECTED> 1st-degree)
Viewer:       http://localhost:8080/viewer/index.html

<If flagged items remain>
Flagged:      <FLAGGED> items need review (unresolved sightings / duplicates)
```
