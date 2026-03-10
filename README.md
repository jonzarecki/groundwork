# Linked Collector

Collects everyone you interact with across Gmail, Calendar, and Slack. Finds their LinkedIn profiles. Gives you a ranked list.

No backend code. No API wrappers. Just an AI agent with MCP servers that does the work for you.

## How it works

```
Gmail + Calendar + Slack  →  AI agent  →  SQLite database  →  HTML viewer
       (MCP servers)         (any MCP agent)   (contacts.db)     (table)
```

Say "collect" or "run". The agent pulls contacts from your communication channels, deduplicates them using persistent matching rules, auto-connects known LinkedIn connections, finds profiles for new contacts, and presents a structured report. Browse results in the HTML viewer or export to CSV.

## Quick start

```bash
# 1. Clone and configure
git clone https://github.com/YOUR_USERNAME/linked-collector.git
cd linked-collector
cp .env.example .env          # Edit LC_SELF_EMAIL with your email

# 2. Set up (creates DB, verifies MCP servers)
./scripts/setup.sh            # Or say "setup" to the agent

# 3. (Optional) Import your LinkedIn connections
#    Open viewer, click "Import LinkedIn", follow the walkthrough
#    Or: ./scripts/import-connections.sh data/Connections.csv

# 4. (Optional) Enable LinkedIn enrichment
uvx linkedin-scraper-mcp --login --no-headless

# 5. Collect!
#    Say "collect" or "run" to your agent
#    (works in Cursor, Claude Code, or any MCP-capable agent)
```

## Weekly workflow

Say **"collect"** (or "run", "collect 14" for 14 days). The agent does everything:

1. **Collects** from Gmail, Calendar, Slack in parallel (~15s)
2. **Resolves** identities via matching rules + creates new contacts (~5s)
3. **Enriches** top new contacts with LinkedIn profiles (~60s, if MCP available)
4. **Reports** new contacts, score movers, stats
5. **Reviews** flagged items (duplicates, incomplete names) -- only if needed

```
=== Weekly Collect Report (last 7 days) ===

New contacts:     8
Score changes:   12
Total:          609 (14 ignored, hidden)
Connected:       17

Top new contacts:
 1. [45] Roy Nissim    rnissim@redhat.com    calendar,slack  connected
 2. [32] Jenny Yi      yyi@redhat.com        calendar,slack  linkedin found
 ...
```

## Prerequisites

- An MCP-capable AI coding agent -- any of these work:
  - [Cursor](https://cursor.com) (reads `.cursor/rules/collect.mdc`)
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (reads `.claude/commands/collect.md`)
  - Any other agent that reads `CLAUDE.md` (Windsurf, Cline, Copilot Chat, etc.)
- At least one of these MCP servers configured:
  - **google-workspace** -- Gmail search/read, Calendar events
  - **slack** -- Slack channels, DMs, user profiles
- `sqlite3` CLI (pre-installed on macOS/Linux)
- `python3` 3.9+ (stdlib only, no pip install needed)

Optional:
- **google-contacts** MCP -- for resolving calendar-only contact names
- **linkedin-scraper-mcp** -- for LinkedIn profile enrichment (`uvx linkedin-scraper-mcp`)

## Commands

| Command | What it does |
|---------|-------------|
| "collect" or "run" | Full pipeline: collect + resolve + enrich + report |
| "collect 14" | Same but for last 14 days |
| "enrich" | LinkedIn enrichment only (top unenriched contacts) |
| "status" | Print database stats |
| "setup" | First-time setup wizard |

In Claude Code, use `/collect`, `/enrich`, `/status`.

## Configuration

Copy `.env.example` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LC_SELF_EMAIL` | (required) | Your email address -- filtered from sightings |
| `LC_MAX_PARTICIPANTS` | `80` | Skip emails/meetings with more participants than this |
| `LC_COLLECT_DAYS` | `7` | Default collection window in days |
| `LC_ENRICH_BATCH_SIZE` | `10` | Max contacts to enrich per collect run |

## Viewer

Open `viewer/index.html` in a browser (serve via `python3 -m http.server 8888`). Features:

- Sortable, filterable contact table
- Click any row to see: raw sightings, matching rules, merge history, LinkedIn searches
- Import LinkedIn connections via drag-and-drop CSV walkthrough
- Ignore/unignore contacts (persists across runs)
- Save modified database back to disk

## Architecture

The pipeline is deterministic scripts + agent judgment:

| Step | Script | Agent needed? |
|------|--------|---------------|
| Parse MCP responses | `scripts/parse-source.py` | No |
| Resolve identities | `scripts/resolve-sightings.sql` | No |
| Update scores/names | `scripts/update-people.sql` | No |
| Auto-connect from CSV | (in resolve-sightings.sql) | No |
| Finalize run | `scripts/finalize-run.sql` | No |
| Merge duplicates | `scripts/merge-people.sh` | Agent decides when |
| Full pipeline | `scripts/process-run.sh` | No |
| Fuzzy matching (B4) | -- | Yes (agent judgment) |
| LinkedIn enrichment | -- | Yes (MCP calls) |

See `ARCH.md` for the full data model and file tree.

## Recovery

If the database is corrupted or you want to start fresh:

```bash
mv data/contacts.db data/contacts.db.bak
./scripts/init-db.sh
# Re-import LinkedIn connections if you had them:
./scripts/import-connections.sh data/Connections.csv
```

## Privacy

All data stays local in the SQLite file. The agent accesses communication tools through MCP servers you configure yourself. No data is sent anywhere except through those MCP connections.

## License

MIT
