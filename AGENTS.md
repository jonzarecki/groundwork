## Learned User Preferences

- Always enrich LinkedIn contacts strictly top-down by `interaction_score DESC`; drain each score tier before moving lower; do not process old pending files first if they are not top-ranked
- When a LinkedIn search returns no results, retry with the full legal name (e.g., "Zachary" instead of "Zack") before marking as unfindable
- Write agent commands/skills in Claude Code format (`.claude/commands/`) only — no need to duplicate as Cursor-specific skill files; Cursor inherits via CLAUDE.md's reference to `.claude/commands/`
- Multi-channel interaction patterns (e.g., meeting + Slack DM) are dramatically stronger signals of real connections than single-channel high-count interactions (e.g., many received emails); scoring should weight channel diversity as a primary ranking factor, not just a tiebreaker
- During LinkedIn enrichment, use `scripts/save-linkedin.sh` (single) or `scripts/save-linkedin-batch.py` (full session) instead of repeating raw SQL per person — dramatically reduces token usage
- Skip WebFetch validation during LinkedIn enrichment when `search_people` already shows: company in headline + 2nd degree + ≥2 relevant mutual connections — these signals are sufficient to save with high confidence
- After committing and pushing code changes, always wait for CI to complete and confirm it is green before declaring the task done

## Learned Workspace Facts

- Google OAuth client must be "Desktop app" type (not "Web Application") in GCP Console to avoid `redirect_uri_mismatch` during the loopback redirect flow in `setup-auth.py`
- `directory.readonly` is a Google restricted scope requiring an expensive third-party security audit (~$15K–75K); it has been removed — only `gmail.readonly`, `calendar.readonly`, and `contacts.readonly` are used
- `LC_PROVIDER=mcp` in `.env` tells `run-collect.sh` to pass `--provider mcp` to `collect-sources.py`; use this when the direct provider credentials are not set up
- Slack `xoxc` lives in Chrome Local Storage (LevelDB), not cookies; `xoxd` is in the cookie DB (extractable via `pycookiecheat`); both tokens are required together for Slack API calls
- Google's "unverified app" warning is expected for `gmail.readonly`, `calendar.readonly`, and `contacts.readonly` (sensitive scopes); CASA Tier 2 assessment (~$550) is required to remove it and is not worth pursuing for personal OSS tools — document the "Advanced → Go to app (unsafe)" click-through for users instead
- `enrich-linkedin.py` auto-logs every search to `linkedin_searches` with candidates JSON; `chosen_url`/`confidence`/`notes` remain NULL until the agent fills them during review; re-runs skip contacts with existing pending JSON
- `scripts/save-linkedin.sh` and `scripts/save-linkedin-batch.py` persist LinkedIn results in one call; `save-linkedin-batch.py --rows '[...]'` accepts a full session's JSON array — use these instead of per-person SQL
- Gmail `email_received` uses `threadId` as `source_ref` so an entire reply thread deduplicates to one sighting; `email_sent` still counts per message (each send is intentional)
- Slack DM `source_ref` is date-bucketed as `{channel_id}_{YYYY-MM-DD}` — one sighting per active day per DM channel (not per message or per week)
- `tests/setup.sh` prompts for confirmation before overwriting `data/contacts.db`; pass `--force` in CI to skip; never omit `--force` in automated runs
- GCP OAuth app `groundwork` (project `groundwork-493305`, owner `yonilx@gmail.com`) is live with bundled client credentials embedded in `scripts/setup-auth.py` — new users need no GCP account; if credentials leak, rotate in GCP Console, scrub history with `git filter-repo --replace-text`, and explicitly unblock via GitHub's secret-scanning URL (push protection blocks all `GOCSPX-` tokens including valid Desktop app embeds)
- `people.is_external` is set during `process-run.sh` by comparing `company_domain` against the home domain extracted from `LC_SELF_EMAIL`; external contacts with any direct interaction receive a `+10 external_direct_bonus` in scoring
