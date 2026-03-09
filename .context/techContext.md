# Tech Context

## Stack
- Runtime: Claude Code (AI agent with MCP servers) -- no traditional application code
- Database: SQLite (accessed via `sqlite3` CLI)
- Viewer: Static HTML + sql.js (WASM-compiled SQLite, loaded client-side)
- Scripts: Bash (init-db, export-csv)

## MCP Dependencies (external, user-configured)
- Google Workspace MCP: Gmail API + Calendar API access
- Slack MCP: Conversations, users, DM history
- Web search: For LinkedIn profile matching (Brave MCP, built-in search, etc.)

## Key Technical Decisions Made
- No backend code: the AI agent IS the pipeline, commands are prompts not scripts
- SQLite over Postgres: local-first, no server, single file, portable
- sql.js for viewer: no backend needed, user opens HTML file and loads .db
- Separate /collect and /enrich commands: collection is cheap (MCP calls), enrichment costs web search quota
- Email as primary dedup key: deterministic, available from all sources
- Metadata only: never store email bodies or message content

## Key Technical Decisions Pending
- Optimal batch size for /enrich (currently defaults to 10)
- Whether to add a "last connected check" against LinkedIn (requires browser MCP)
- How to handle Slack workspaces where email is not visible in user profiles
