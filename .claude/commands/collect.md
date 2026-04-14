Collect contacts from Gmail, Calendar, and Slack for the last $ARGUMENTS days (default: LC_COLLECT_DAYS from .env, or 7).

## Overview

One script handles collection, processing, and reporting. The agent only intervenes for LinkedIn enrichment review and flagged items.

```
Phase 1-4: ./scripts/run-collect.sh (automated)
Phase 3b:  LinkedIn enrichment (script + agent review)
Phase 5:   Review flagged items (agent judgment)
```

## Phase 1-4: Collect + Process + Report

```bash
./scripts/run-collect.sh $ARGUMENTS
```

This single command handles everything:
- Pre-flight checks (env, DB)
- Creates run record
- Collects from Gmail, Calendar, Slack via direct API scripts (zero agent tokens)
- Enriches names from Google Contacts directory
- Resolves sightings (B1-B5 cascade)
- Auto-merges obvious duplicates
- Prints formatted report

Show the output to the user.

## Phase 3b: LinkedIn Enrichment (optional)

```bash
python3 scripts/enrich-linkedin.py [--batch-size 10]
```

The script searches LinkedIn for unenriched contacts and saves raw responses to `data/tmp/linkedin/`. Then the agent reviews each result:

1. Read each `data/tmp/linkedin/<person_id>.json` file
2. Evaluate: does the name match? company? connection degree?
3. If confident match: UPDATE `people` with `linkedin_url`, INSERT into `linkedin_searches`
4. If 1st degree + company matches: also set `status = 'connected'`

If the script fails due to missing deps, use the LinkedIn MCP `search_people` tool directly. Always process contacts in `interaction_score DESC` order — drain higher scores first. Retry with the full legal name if a nickname returns no results.

Skip this phase if no LinkedIn access is available.

## Phase 5: Review (only if flagged)

Check the report output for flagged items. Only intervene if `Flagged for review > 0`.

### B4 fuzzy candidates
Present unresolved sightings with potential matches. For each: match to existing person (INSERT `name_domain` rule) or skip.

### Remaining duplicates
Obvious duplicates are auto-merged by `run-collect.sh`. Only ambiguous cases remain:
```bash
./scripts/merge-people.sh --keep <id> --merge <id> --reason "..."
```

If no flagged items, the run is complete.

## Important

- All filtering: `parse-source.py`
- All resolution: `resolve-sightings.sql`
- All scoring: `update-people.sql`
- Agent judgment: only LinkedIn match evaluation, B4 fuzzy matching, ambiguous merges
- Never store email body content or Slack message text
