-- =============================================================================
-- Groundwork -- SQLite Schema
-- =============================================================================
-- Nine tables:
--   people              Canonical deduplicated person (the product)
--   sightings           Raw contact appearances from sources (replaces interactions)
--   matching_rules      Explicit identity resolution rules (email, slack_uid, name_domain)
--   merge_log           Audit trail for person merges
--   linkedin_searches   Enrichment traceability (search queries, candidates, choices)
--   runs                Collection/enrichment run bookkeeping
--   linkedin_connections Imported LinkedIn 1st-degree connections (CSV)
--   contact_names        Cached email-to-name lookups from Google Contacts directory
-- =============================================================================

-- ---------------------------------------------------------------------------
-- people: one row per deduplicated person
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    company TEXT,
    company_domain TEXT,
    linkedin_url TEXT,
    linkedin_confidence TEXT CHECK(linkedin_confidence IN ('high', 'medium', 'low')),
    interaction_score INTEGER DEFAULT 0,
    channel_diversity INTEGER DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    sources TEXT NOT NULL,
    status TEXT DEFAULT 'new' CHECK(status IN ('new', 'reviewed', 'connected', 'ignored')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_people_email ON people(email);
CREATE INDEX IF NOT EXISTS idx_people_score ON people(interaction_score DESC);
CREATE INDEX IF NOT EXISTS idx_people_last_seen ON people(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_people_status ON people(status);
CREATE INDEX IF NOT EXISTS idx_people_linkedin ON people(linkedin_url) WHERE linkedin_url IS NOT NULL;

-- ---------------------------------------------------------------------------
-- sightings: every raw contact appearance from a source
-- Replaces the old `interactions` table. Carries both interaction data
-- (type, timestamp, context) and raw identity data (name, email, company
-- as extracted from the source). raw_* fields are immutable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    source TEXT NOT NULL CHECK(source IN ('gmail', 'calendar', 'slack')),
    source_ref TEXT,
    source_uid TEXT,
    raw_name TEXT,
    raw_email TEXT,
    raw_company TEXT,
    raw_title TEXT,
    raw_username TEXT,
    interaction_type TEXT NOT NULL CHECK(interaction_type IN (
        'email_sent', 'email_received', 'meeting', 'slack_dm', 'slack_channel'
    )),
    is_group INTEGER NOT NULL DEFAULT 0,
    interaction_at TEXT NOT NULL,
    context TEXT,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    match_method TEXT CHECK(match_method IN (
        'exact_email', 'exact_source_uid', 'fuzzy_name', 'agent_judgment', 'manual'
    )),
    match_confidence TEXT CHECK(match_confidence IN ('high', 'medium', 'low')),
    matched_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sightings_person ON sightings(person_id);
CREATE INDEX IF NOT EXISTS idx_sightings_run ON sightings(run_id);
CREATE INDEX IF NOT EXISTS idx_sightings_raw_email ON sightings(raw_email) WHERE raw_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sightings_source_uid ON sightings(source, source_uid);
CREATE INDEX IF NOT EXISTS idx_sightings_source_time ON sightings(source, interaction_at DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_unresolved ON sightings(person_id) WHERE person_id IS NULL;

-- ---------------------------------------------------------------------------
-- matching_rules: explicit identity resolution rules
-- The agent writes rules here; they are applied automatically in future runs.
-- UNIQUE constraint enforces one person per identifier.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matching_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL CHECK(identifier_type IN ('email', 'slack_uid', 'name_domain')),
    identifier_value TEXT NOT NULL,
    source TEXT,
    created_by_run_id INTEGER REFERENCES runs(id),
    confidence TEXT NOT NULL CHECK(confidence IN ('high', 'medium', 'low')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(identifier_type, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_matching_rules_lookup ON matching_rules(identifier_type, identifier_value);
CREATE INDEX IF NOT EXISTS idx_matching_rules_person ON matching_rules(person_id);

-- ---------------------------------------------------------------------------
-- merge_log: audit trail for person merges
-- Snapshots the merged (deleted) person as JSON before deletion.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merge_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kept_person_id INTEGER NOT NULL REFERENCES people(id),
    merged_person_id INTEGER NOT NULL,
    merged_person_snapshot TEXT,
    reason TEXT NOT NULL,
    run_id INTEGER REFERENCES runs(id),
    merged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_merge_log_kept ON merge_log(kept_person_id);

-- ---------------------------------------------------------------------------
-- linkedin_searches: every LinkedIn search attempt with candidates and choice
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS linkedin_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id),
    search_query TEXT NOT NULL,
    candidates TEXT,
    chosen_url TEXT,
    confidence TEXT CHECK(confidence IN ('high', 'medium', 'low')),
    notes TEXT,
    searched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_linkedin_searches_person ON linkedin_searches(person_id);

-- ---------------------------------------------------------------------------
-- runs: bookkeeping for collection and enrichment runs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT NOT NULL CHECK(source IN ('gmail', 'calendar', 'slack', 'all', 'enrich')),
    contacts_found INTEGER DEFAULT 0,
    contacts_new INTEGER DEFAULT 0,
    contacts_updated INTEGER DEFAULT 0,
    notes TEXT
);

-- ---------------------------------------------------------------------------
-- slack_users: cache of Slack user lookups to avoid redundant MCP calls
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slack_users (
    slack_uid TEXT PRIMARY KEY,
    username TEXT,
    real_name TEXT,
    email TEXT,
    title TEXT,
    fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- linkedin_connections: imported from LinkedIn CSV export
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS linkedin_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    linkedin_url TEXT UNIQUE,
    email TEXT,
    company TEXT,
    position TEXT,
    connected_on TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_linkedin_connections_url ON linkedin_connections(linkedin_url);
CREATE INDEX IF NOT EXISTS idx_linkedin_connections_email ON linkedin_connections(email);

-- ---------------------------------------------------------------------------
-- contact_names: cached email-to-name lookups from Google Contacts directory
-- Avoids redundant search_directory MCP calls on subsequent collect runs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contact_names (
    email TEXT PRIMARY KEY,
    name TEXT,
    title TEXT,
    fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
