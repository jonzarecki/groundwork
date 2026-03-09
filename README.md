# Linked Collector

Collects everyone you interact with across Gmail, Calendar, and Slack. Finds their LinkedIn profiles. Gives you a ranked list.

No backend. No API code. No cron jobs. Just an AI agent (Claude Code) with MCP servers that does the work for you.

## How it works

```
Gmail + Calendar + Slack  →  Claude Code agent  →  SQLite database  →  HTML viewer
         (MCP servers)           (commands)           (contacts.db)       (table)
```

You run a command. The agent reads your communication channels through MCP servers, collects every person you've interacted with, deduplicates them, finds their LinkedIn profiles via web search, and stores everything in a local SQLite file. You browse the results in a single-page HTML viewer or export to CSV.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and working
- Google Workspace MCP server configured (for Gmail + Calendar access)
- Slack MCP server configured (for Slack access)
- A web search capability available to the agent (built-in, Brave MCP, etc.)
- `sqlite3` CLI available (pre-installed on macOS/Linux)

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/linked-collector.git
cd linked-collector
./scripts/init-db.sh
```

That's it. The database is created at `data/contacts.db`.

## Usage

### Collect contacts

```bash
claude /collect        # Collect from all sources (last 7 days)
claude /collect 30     # Collect from last 30 days
```

The agent will:
1. Pull emails from Gmail (senders + recipients)
2. Pull meeting attendees from Calendar
3. Pull DM partners and channel activity from Slack
4. Deduplicate across all sources
5. Store results in the database

### Find LinkedIn profiles

```bash
claude /enrich         # Enrich up to 10 people
claude /enrich 25      # Enrich up to 25 people
```

The agent will search `site:linkedin.com/in "{name}" "{company}"` for each person without a LinkedIn URL, evaluate the results, and store the best match with a confidence score (high/medium/low).

### Check status

```bash
claude /status
```

Prints a summary: total contacts, LinkedIn coverage, source breakdown, top unlinked contacts, recent runs.

### Browse results

Open `viewer/index.html` in your browser and load the `data/contacts.db` file. Sortable, filterable table with clickable LinkedIn links.

### Export to CSV

```bash
./scripts/export-csv.sh                    # Default: data/contacts.csv
./scripts/export-csv.sh ~/Desktop/out.csv  # Custom path
```

## What this is

A personal tool for staying on top of your professional network. Run it after conferences, weekly, or whenever you feel like it.

## What this is NOT

- Not a CRM
- Not a LinkedIn automation tool (no auto-invites)
- Not a web service (runs locally, no deployment)
- Not an "AI insights" platform (just a table)

## Contributing

The project uses an AI-native development workflow. If you open this repo in Claude Code:

```bash
claude /plan      # See what to work on next
claude /review    # Review staged changes before committing
claude /status    # Database stats
```

Key files for orientation:
- `SPEC.md` -- what this tool does and why
- `ARCH.md` -- file tree, components, data model
- `TASKS.md` -- what's done, what's next
- `.context/` -- working memory for AI sessions

## Privacy

All data stays local. The agent accesses your communication tools through MCP servers you configure yourself. No data is sent anywhere except through those MCP connections and web search queries for LinkedIn matching.

## License

MIT
