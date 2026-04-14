# New User Test Protocol

Tests that an AI agent (Cursor, Claude Code) can successfully onboard a new user
from a fresh clone with zero prior context.

## Prerequisites

- Fresh clone of the repo (or `git clean -fdx` to simulate)
- No `.env`, no `data/` directory
- Agent session with no prior conversation history about this project

## Test Scenarios

### Scenario A: Install + First Collect (happy path)

**Setup:**
```bash
git clean -fdx      # remove all untracked files including .env and data/
git checkout .      # restore tracked files
```

**Prompt to agent:**
```
install
```

**Expected agent behavior (in order):**
1. Runs `python3 scripts/setup-auth.py --check` to assess current state
2. Detects missing deps, runs `pip install -e ".[direct]"` (or `uv pip install`)
3. Creates `.env` from `.env.example`, asks user for `LC_SELF_EMAIL`
4. Runs `python3 scripts/setup-auth.py google` — opens browser for OAuth
5. Optionally sets up Slack/LinkedIn (should present as optional, not block on failure)
6. Runs `./scripts/setup.sh` — creates DB
7. Offers to run first collection

**Pass criteria:**
- [ ] Agent completed install without the user having to look up any commands
- [ ] Google OAuth prompted once (no re-runs)
- [ ] Slack/LinkedIn presented as optional (not hard failures)
- [ ] `data/contacts.db` exists and has the 9 tables
- [ ] Agent did not call any MCP tools directly during install

**Fail patterns to watch for:**
- Agent runs `setup-auth.sh` (legacy) instead of `setup-auth.py`
- Agent tries to configure Docker or `local-automation-mcp`
- Agent blocks waiting for Slack/LinkedIn instead of skipping them
- Agent calls Gmail/Calendar MCP tools before auth is set up

---

### Scenario B: Fresh Collect

**Setup:** After Scenario A completes (auth configured, DB initialized)

**Prompt to agent:**
```
collect
```

**Expected agent behavior:**
1. Runs `./scripts/run-collect.sh` (single command, not individual API calls)
2. Shows the full collect report output to the user
3. If `Flagged for review > 0`: presents flagged items, waits for input
4. If `Flagged for review = 0`: declares run complete

**Verification:**
```bash
./tests/verify-agent.sh
```

**Pass criteria:**
- [ ] `data/contacts.db` has contacts (sources: gmail, calendar, slack)
- [ ] Agent showed the "=== Collect Report ===" output
- [ ] `verify-agent.sh` exits 0

**Fail patterns:**
- Agent calls Gmail MCP tools directly (should use the script)
- Agent calls collect multiple times
- Agent runs enrich without being asked
- Agent makes up contact counts instead of showing actual script output

---

### Scenario C: LinkedIn Enrichment

**Setup:** After Scenario B (contacts in DB)

**Prompt to agent:**
```
enrich
```

**Expected agent behavior:**
1. Queries `SELECT ... ORDER BY interaction_score DESC` to get top candidates
2. Searches LinkedIn in descending score order (highest score first)
3. For each: evaluates match (name + company), saves to DB, logs to `linkedin_searches`
4. Never guesses LinkedIn URL slugs from names
5. Stops when batch is done, reports count

**Pass criteria:**
- [ ] Enrichment order matches `interaction_score DESC`
- [ ] Each search logged in `linkedin_searches` with `candidates` JSON
- [ ] No guessed URLs (all URLs come from search results `references` field)
- [ ] `verify-agent.sh` exits 0

**Fail patterns:**
- Agent enriches in random order (not by score)
- Agent constructs a LinkedIn URL as `linkedin.com/in/firstname-lastname` without search
- Agent skips `linkedin_searches` logging
- Agent calls `enrich-linkedin.py` but it fails due to missing `linkedin-api` dep — should fall back to MCP `search_people`

---

### Scenario D: Fresh Session Context Test

Tests that `CLAUDE.md` alone is sufficient for a brand-new agent session.

**Setup:** Start a completely new agent conversation (no history)

**Prompt:**
```
What's the status of the Groundwork database?
```

**Pass criteria:**
- [ ] Agent runs `./scripts/status.sh` (not raw SQL)
- [ ] Agent references correct table names (`sightings` not `interactions`)
- [ ] Agent knows scoring weights without asking (from CLAUDE.md)

---

## Automated Checks

The following run without an AI agent:

```bash
# Layer 1: script + schema tests (fast, no auth)
./scripts/test-pipeline.sh

# Layer 1: fixture data pipeline test
./tests/setup.sh
./scripts/process-run.sh $(cat data/tmp/test_run_id)
./tests/verify.sh

# Layer 2: after running an agent collect (requires live agent first)
./tests/verify-agent.sh
```

## Scoring

| Check | Weight |
|-------|--------|
| `test-pipeline.sh` passes | Required |
| `verify.sh` passes after fixture run | Required |
| Scenario A completes without user looking up commands | High |
| Scenario B: agent uses `run-collect.sh`, not MCP directly | High |
| Scenario C: enrichment order is score-descending | Medium |
| Scenario D: fresh session uses correct table names | Medium |
| `verify-agent.sh` exits 0 | High |
