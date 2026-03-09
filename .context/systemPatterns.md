# System Patterns

## General Conventions
- Conventional commits
- Update `.context/progress.md` after completing tasks
- Reference `SPEC.md` for product requirements
- Reference `ARCH.md` for file tree and data model

## Command Patterns
- Commands are markdown prompts in `.claude/commands/`
- Each command is self-contained: reads the DB, does work, writes to DB, prints summary
- Commands accept positional arguments via `$ARGUMENTS` (e.g., `/collect 30` for 30 days)
- Commands should degrade gracefully if an MCP is unavailable (skip that source, don't fail)

## Database Patterns
- All DB access through `sqlite3` CLI (no drivers, no ORM)
- ISO 8601 timestamps everywhere: `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')`
- Email is the primary dedup key (UNIQUE constraint on people.email)
- Interaction score is always recalculated from interactions table, not incremented
- Every collection adds interaction rows even for existing people

## Naming
- Commands: verb form (`collect`, `enrich`, `status`)
- Tables: plural nouns (`people`, `interactions`, `runs`)
- Columns: snake_case
- Timestamps: ISO 8601 with Z suffix
- Sources: lowercase (`gmail`, `calendar`, `slack`)
- Interaction types: `source_action` pattern (`email_sent`, `slack_dm`, `meeting`)
