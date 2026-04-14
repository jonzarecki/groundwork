First-time setup wizard for Groundwork. Walk the user through installing dependencies, configuring credentials, and running their first collection. Handle each step interactively — ask questions, explain what's happening, and handle failures gracefully.

## Step 1 — Check current state

Run `python3 scripts/setup-auth.py --check` and read the output. Use it to determine which steps are already complete and skip them.

## Step 2 — Install dependencies

Check whether dependencies are already installed:

```bash
python3 -c "import googleapiclient, pycookiecheat" 2>/dev/null && echo "ok" || echo "missing"
```

If missing: check whether `uv` is available (`which uv`). If yes, run:

```bash
uv pip install -e ".[direct]"
```

Otherwise:

```bash
pip install -e ".[direct]"
```

Show the install output. If it fails, stop and report the error clearly.

## Step 3 — Configure .env

Read `.env` if it exists, otherwise read `.env.example`.

- If `LC_SELF_EMAIL` is missing or still `you@example.com`: ask the user for their email address, then write it to `.env`.
- If `LC_SLACK_WORKSPACE` is missing or still `mycompany`: ask whether the user wants Slack collection (see Step 6). If yes, ask for their Slack workspace subdomain (e.g. `mycompany` for `mycompany.slack.com`) and write it to `.env`.

If `.env` does not exist yet, create it from `.env.example` first.

## Step 4 — Init database

```bash
./scripts/setup.sh
```

Show the output. If it reports missing tables or errors, stop and report clearly.

## Step 5 — Google OAuth

Tell the user:

> "A browser window is about to open. Sign in with your Google account and grant read access to Gmail, Calendar, and Contacts. Come back here when the browser shows 'Authentication successful'."

Then run:

```bash
python3 scripts/setup-auth.py google
```

Wait for it to complete. If it fails, show the error and stop — Google auth is required.

## Step 6 — Slack tokens (optional)

Ask the user: **"Do you use Slack at work? Gmail + Calendar alone is enough for basic use — Slack adds DMs and channel mentions to your contact scores."**

If **no**: skip this step. Note that `collect-sources.py` will log `Slack: FAILED` during collection but continue normally — no action needed.

If **yes**:
1. Ask for `LC_SLACK_WORKSPACE` if not already set (Step 3 may have handled this).
2. Tell the user: *"Make sure you are currently logged into Slack in Chrome."*
3. Run:
   ```bash
   python3 scripts/setup-auth.py slack
   ```
4. If it fails with a cookie extraction error, automatically retry in manual mode:
   ```bash
   python3 scripts/setup-auth.py slack --manual
   ```
   The manual mode will print step-by-step DevTools instructions. Guide the user through them.

## Step 7 — LinkedIn enrichment (optional)

Ask the user: **"Do you want LinkedIn profile enrichment? This finds LinkedIn URLs for your contacts. Requires Chrome logged into LinkedIn."**

If **yes**:
1. Tell the user: *"Make sure you are currently logged into LinkedIn in Chrome."*
2. Run:
   ```bash
   python3 scripts/setup-auth.py linkedin
   ```
3. If it fails, offer the manual fallback:
   ```bash
   python3 scripts/setup-auth.py linkedin --manual
   ```
   Guide the user through the manual steps if needed.

If **no**: skip. LinkedIn enrichment can be set up later by running `/install` again or `python3 scripts/setup-auth.py linkedin`.

## Step 8 — Verify and offer first run

Run:

```bash
python3 scripts/setup-auth.py --check
```

Show the summary. For any failed service the user opted into, suggest the fix command.

Then ask: **"Ready to run your first collection? This will collect contacts from the last 7 days."**

If yes:

```bash
./scripts/run-collect.sh
```

Show the output and summarise the results (new contacts found, top contacts by score).

## Important

- Never abort the entire setup because one optional service (Slack, LinkedIn) failed. Only Google auth is required.
- If the user is re-running setup (credentials already exist), confirm before overwriting.
- All credentials are saved to `data/.credentials/` (gitignored). Nothing is sent anywhere beyond the OAuth/API calls.
