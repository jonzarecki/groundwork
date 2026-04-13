## Learned User Preferences

- Always enrich LinkedIn contacts strictly top-down by `interaction_score DESC`; drain each score tier before moving lower; do not process old pending files first if they are not top-ranked
- When a LinkedIn search returns no results, retry with the full legal name (e.g., "Zachary" instead of "Zack") before marking as unfindable

## Learned Workspace Facts

- Google OAuth client must be "Desktop app" type (not "Web Application") in GCP Console to avoid `redirect_uri_mismatch` during the loopback redirect flow in `setup-auth.py`
- `directory.readonly` is a Google restricted scope requiring an expensive third-party security audit (~$15K–75K); it has been removed — only `gmail.readonly`, `calendar.readonly`, and `contacts.readonly` are used
- `LC_PROVIDER=mcp` in `.env` tells `run-collect.sh` to pass `--provider mcp` to `collect-sources.py`; use this when the direct provider credentials are not set up
- Slack `xoxc` lives in Chrome Local Storage (LevelDB), not cookies; `xoxd` is in the cookie DB (extractable via `pycookiecheat`); both tokens are required together for Slack API calls
