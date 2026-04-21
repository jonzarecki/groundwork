First-time setup wizard for Groundwork. Goal: get the user to their first collection as fast as possible. Google is the only hard requirement — everything else is a value-add they can layer in later.

## Step 1 — Check what's already done

Run this first, before anything else:

```bash
python3 scripts/setup-auth.py --check
```

Show the output to the user as-is. Use it to skip any steps that are already complete. If everything is green and a DB exists, jump straight to offering `/start`.

---

## Phase A — Required (3 steps to first collection)

### Step 2 — Install dependencies

Check whether already installed:

```bash
python3 -c "import googleapiclient, pycookiecheat" 2>/dev/null && echo "ok" || echo "missing"
```

If already installed: skip silently.

If missing, use `uv` if available, otherwise `pip`:

```bash
uv pip install -e ".[direct]"   # if uv is available
# OR
pip install -e ".[direct]"
```

If it fails, stop and show the error — do not continue.

### Step 3 — Your email address

Read `.env` (or `.env.example` if `.env` doesn't exist yet).

If `LC_SELF_EMAIL` is missing or still `you@example.com`: ask the user for their email address, write it to `.env`, done.

If already set: skip silently.

### Step 4 — Google auth

First check which provider is set in `.env` (`LC_PROVIDER`). Default to `direct` if unset.

**If `LC_PROVIDER=direct`:**

Tell the user:

> "Opening a browser window — sign in with your Google account and grant read access to Gmail, Calendar, and Contacts. Come back here when the browser shows 'Authentication successful'."

```bash
python3 scripts/setup-auth.py google
```

**Corporate Google Workspace accounts** (e.g. `@redhat.com`, `@company.com`) will likely be **blocked** by their admin with `Error 400: access_not_configured`. This is a Workspace admin policy — the OAuth app hasn't been approved for that domain. If this happens:

> "Your corporate Google account blocks third-party OAuth apps. You have two alternatives:
> - **Google Workspace MCP** (recommended): run the Docker MCP stack in `local-automation-mcp/` and set `LC_PROVIDER=mcp` in `.env`. This uses browser session tokens instead of OAuth and bypasses the restriction.
> - **Google Cloud CLI**: authenticate with `gcloud auth application-default login --scopes=...` using a GCP project that your org already trusts, then set `LC_PROVIDER=direct` — the direct provider will pick up the ADC credentials.
>
> For personal Gmail accounts, direct OAuth works fine."

If `LC_PROVIDER=mcp`, skip `setup-auth.py` entirely. Instead, confirm the Docker MCP stack is running:

```bash
curl -sf http://localhost:9090/google-workspace/sse > /dev/null && echo "MCP stack up" || echo "MCP stack not running"
```

If not running, tell the user to start it:
```bash
# In sibling repo local-automation-mcp/
docker-compose up -d
```

Google auth is the only hard blocker — do not continue until one of the two paths is confirmed working.

---

## First run

Once Steps 2-4 are done (or were already done), say:

> "You're set up. Running your first collection now — this pulls contacts from the last 7 days."

Then run the full start flow without asking:

```bash
./scripts/run-collect.sh
```

Show the output. After it completes, launch the viewer:

```bash
python3 scripts/server.py &
```

Print a clean summary:

```
✓ Setup complete.

  Contacts found:  <N> (from Gmail + Calendar)
  Viewer:          http://localhost:8080/viewer/index.html

Say "start" any time to collect and refresh your contacts.
```

Stop here. Do not ask about Slack or LinkedIn yet — offer them as follow-ups below.

---

## Phase B — Value-adds (offer after first run)

Only offer these after the user has seen their first results. Present them as enhancements, not steps.

> "Want to get more out of Groundwork? Two optional add-ons:"
>
> - **Slack** — adds DMs and channel mentions to contact scores. Requires Chrome logged into Slack.
> - **LinkedIn** — finds LinkedIn profiles for your contacts. Requires Chrome logged into LinkedIn.
>
> Say "add slack", "add linkedin", or "skip" to do later.

### Adding Slack

1. Ask for `LC_SLACK_WORKSPACE` if not set (the subdomain, e.g. `mycompany` for `mycompany.slack.com`). Write it to `.env`.
2. Tell the user: *"Make sure you're logged into Slack in Chrome."*
3. Run:
   ```bash
   python3 scripts/setup-auth.py slack
   ```
4. If it fails with a cookie extraction error, retry automatically:
   ```bash
   python3 scripts/setup-auth.py slack --manual
   ```
   The manual mode prints step-by-step DevTools instructions — guide the user through them.
5. On success: `✓ Slack added. Your next "start" will include DMs and channel mentions.`

### Adding LinkedIn

Check if a `linkedin` MCP server is already configured in `.mcp.json` or `.cursor/mcp.json`:

```bash
python3 -c "import json,pathlib; cfg=pathlib.Path('.mcp.json'); print('MCP configured' if cfg.exists() and 'linkedin' in json.loads(cfg.read_text()).get('mcpServers',{}) else 'not configured')"
```

**If a LinkedIn MCP is already configured** (e.g. `linkedin-scraper-mcp` via `uvx`): no cookie setup needed — the agent uses it directly. Tell the user:
> `✓ LinkedIn MCP detected. Profile search is ready — your next "start" will find LinkedIn profiles for your contacts.`

**If not configured**, offer two options:

*Option A — LinkedIn MCP (recommended, no cookie needed):*
```bash
uvx linkedin-scraper-mcp --login --no-headless
```
Then add to `.mcp.json`:
```json
"linkedin": { "command": "uvx", "args": ["linkedin-scraper-mcp"] }
```

*Option B — Cookie-based fallback:*
1. Tell the user: *"Make sure you're logged into LinkedIn in Chrome."*
2. Run:
   ```bash
   python3 scripts/setup-auth.py linkedin
   ```
3. If it fails, offer the manual fallback:
   ```bash
   python3 scripts/setup-auth.py linkedin --manual
   ```

On success either way: `✓ LinkedIn added. Your next "start" will find profile URLs for your contacts.`

Both can be added later at any time by saying "install" again or running `python3 scripts/setup-auth.py slack|linkedin`.

---

## Important

- Only Google auth is a hard blocker. Never abort setup because Slack or LinkedIn failed.
- If the user is re-running setup with credentials already present, confirm before overwriting.
- All credentials are saved to `data/.credentials/` (gitignored). Nothing is sent anywhere beyond the OAuth/API calls.
- If `setup-auth.py --check` shows everything green, skip straight to offering `/start` — no wizard needed.
