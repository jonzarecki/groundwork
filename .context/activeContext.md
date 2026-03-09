# Active Context

## Current Focus
Phase 6 (Traceability) is complete and fully E2E tested. The sighting-first pipeline with matching_rules auto-resolution is working in production.

## Status
- Schema: 7 tables, migrated, validated
- Resolution Protocol: B1 email rules, B1b people.email fallback, B2 slack_uid, B4 agent judgment, B5 new person
- Auto-resolve verified: run 4 resolved 102/102 sightings via B1 rules alone (zero fallback)
- Enrichment: linkedin_searches logging tested (2 found, 1 failed -- all logged)
- Config: `.env` with `LC_MAX_PARTICIPANTS=80`
- Database: 601 people, 1115 sightings, 108 rules, 3 linkedin_searches, 1 merge_log

## What's next
- Test viewer with populated database (click-to-expand detail panel)
- Test export-csv.sh
- Tune scoring weights based on real data
- Run a full week of collections to build rule coverage
- Consider: commit all changes and push
