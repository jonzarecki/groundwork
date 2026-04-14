# Groundwork

Collects everyone you interact with across Gmail, Calendar, and Slack. Finds their LinkedIn profiles. Gives you a ranked list of your professional contacts.

All data stays local. No backend, no cloud sync.

```
Gmail + Calendar + Slack  →  scripts (direct API)  →  SQLite database  →  HTML viewer
```

## Quick start

Open this project in Cursor, Claude Code, or any coding agent and say:

```
install
```

The agent walks you through everything: installing dependencies, connecting Google (OAuth browser popup), optionally setting up Slack and LinkedIn, initialising the database, and running your first collection.

### Manual setup

```bash
# 1. Install dependencies
pip install -e ".[direct]"

# 2. Configure
cp .env.example .env          # Set LC_SELF_EMAIL and LC_SLACK_WORKSPACE

# 3. Authenticate (one-time, Google is required -- Slack/LinkedIn are optional)
python3 scripts/setup-auth.py google   # Opens browser for Google OAuth
# ⚠️  Google will show "This app isn't verified" -- this is expected for personal tools.
# Click "Advanced" → "Go to Groundwork (unsafe)" to continue.
# Groundwork only reads Gmail, Calendar, and Contacts (never writes or sends anything).

python3 scripts/setup-auth.py slack    # Optional: extracts tokens from Chrome
python3 scripts/setup-auth.py linkedin # Optional: extracts li_at cookie from Chrome

# 4. Init database
./scripts/setup.sh

# 5. Collect!
./scripts/run-collect.sh
```

> **Minimum setup:** Google only (Gmail + Calendar). Slack and LinkedIn are optional and can be added later.

> **Chrome required** for Slack and LinkedIn cookie extraction. macOS and Linux only.

## Weekly workflow

Say **"collect"** (or "run", "collect 14" for 14 days). The agent does everything:

1. **Collects** from Gmail, Calendar, and optionally Slack (~15s)
2. **Resolves** identities via matching rules + creates new contacts (~5s)
3. **Reports** new contacts, score movers, stats

Then optionally say **"enrich"** to find LinkedIn profiles for top contacts.

```
=== Collect Report (last 7 days) ===

New contacts:     8
Sightings:       87
Total:          609 (2 ignored)
With LinkedIn:   17
Connected:       12

Top new contacts:
  [45] Roy Nissim    rnissim@redhat.com    calendar,slack
  [32] Jenny Yi      yyi@redhat.com        calendar,slack
  ...
```

## Prerequisites

- Python 3.9+
- `pip` (for `pip install -e ".[direct]"`)
- `sqlite3` CLI (pre-installed on macOS/Linux)
- A Google account (Gmail + Calendar access)
- Chrome browser (only needed for Slack and LinkedIn token extraction)

Optional:
- Slack (for DMs and channel mentions)
- LinkedIn (for profile enrichment and connection status)

## Commands

| Command | What it does |
|---------|-------------|
| "collect" or "run" | Full pipeline: collect + resolve + report |
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
| `LC_PROVIDER` | `direct` | `direct` (OAuth + cookies) or `mcp` (legacy Docker stack) |
| `LC_SLACK_WORKSPACE` | (required for Slack) | Your Slack workspace subdomain (e.g. `mycompany`) |
| `LC_MAX_PARTICIPANTS` | `80` | Skip emails/meetings with more participants than this |
| `LC_COLLECT_DAYS` | `7` | Default collection window in days |
| `LC_ENRICH_BATCH_SIZE` | `10` | Max contacts to enrich with LinkedIn per run |

## Viewer

Run the dev server and open the viewer:

```bash
python3 scripts/server.py    # serves at http://localhost:8080
# then open: http://localhost:8080/viewer/index.html
```

Features:
- Sortable, filterable contact table
- Click any row to see: raw sightings, matching rules, merge history, LinkedIn searches
- Import LinkedIn connections via drag-and-drop CSV walkthrough
- Ignore/unignore contacts (persists across runs)
- Auto-saves database changes

## Architecture

The pipeline is deterministic scripts + agent judgment:

| Step | Script | Agent needed? |
|------|--------|---------------|
| Collect from sources | `scripts/collect-sources.py` | No |
| Parse raw responses | `scripts/parse-source.py` | No |
| Resolve identities | `scripts/resolve-sightings.sql` | No |
| Update scores/names | `scripts/update-people.sql` | No |
| Auto-merge duplicates | `scripts/auto-merge.sh` | No |
| Full pipeline | `scripts/run-collect.sh` | No |
| Fuzzy name matching | -- | Yes (agent judgment) |
| LinkedIn enrichment | `scripts/enrich-linkedin.py` | Yes (reviews candidates) |

See `ARCH.md` for the full data model and file tree.

## Recovery

If the database is corrupted or you want to start fresh:

```bash
mv data/contacts.db data/contacts.db.bak
./scripts/init-db.sh
# Re-import LinkedIn connections if you had them:
./scripts/import-connections.sh data/Connections.csv
```

## Advanced: MCP provider

If you have a local Docker stack (via `local-automation-mcp`), you can use MCP servers instead:

```bash
LC_PROVIDER=mcp ./scripts/run-collect.sh
```

See `CLAUDE.md` for full MCP configuration details.

## Privacy

All data stays local in `data/contacts.db`. Groundwork reads Gmail, Calendar, and Contacts via the Google OAuth app — it never writes, sends, or shares anything. No data is sent anywhere except through the Google and Slack APIs you configure yourself.

## License

MIT
