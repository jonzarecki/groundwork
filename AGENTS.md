## Learned User Preferences

- Always enrich LinkedIn contacts strictly top-down by `interaction_score DESC`; drain each score tier before moving lower; do not process old pending files first if they are not top-ranked
- When a LinkedIn search returns no results, retry with the full legal name (e.g., "Zachary" instead of "Zack") before marking as unfindable
- Write agent commands/skills in Claude Code format (`.claude/commands/`) only — no need to duplicate as Cursor-specific skill files; Cursor inherits via CLAUDE.md's reference to `.claude/commands/`
- Multi-channel interaction patterns (e.g., meeting + Slack DM) are dramatically stronger signals of real connections than single-channel high-count interactions (e.g., many received emails); scoring should weight channel diversity as a primary ranking factor, not just a tiebreaker

## Learned Workspace Facts

- Google OAuth client must be "Desktop app" type (not "Web Application") in GCP Console to avoid `redirect_uri_mismatch` during the loopback redirect flow in `setup-auth.py`
- `directory.readonly` is a Google restricted scope requiring an expensive third-party security audit (~$15K–75K); it has been removed — only `gmail.readonly`, `calendar.readonly`, and `contacts.readonly` are used
- `LC_PROVIDER=mcp` in `.env` tells `run-collect.sh` to pass `--provider mcp` to `collect-sources.py`; use this when the direct provider credentials are not set up
- Slack `xoxc` lives in Chrome Local Storage (LevelDB), not cookies; `xoxd` is in the cookie DB (extractable via `pycookiecheat`); both tokens are required together for Slack API calls
- Google's "unverified app" warning is expected for `gmail.readonly`, `calendar.readonly`, and `contacts.readonly` (sensitive scopes); CASA Tier 2 assessment (~$550) is required to remove it and is not worth pursuing for personal OSS tools — document the "Advanced → Go to app (unsafe)" click-through for users instead
- `enrich-linkedin.py` auto-logs every search to `linkedin_searches` with candidates JSON; `chosen_url`/`confidence`/`notes` remain NULL until the agent fills them during review
- `enrich-linkedin.py` preserves pending review files in `data/tmp/linkedin/` — re-runs skip contacts with existing JSON instead of wiping unreviewed results
