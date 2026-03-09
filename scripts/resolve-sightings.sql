-- resolve-sightings.sql
-- Resolves all unresolved sightings via the B1-B5 cascade.
-- Run after inserting sightings: sqlite3 data/contacts.db < scripts/resolve-sightings.sql
-- Pure SQL, no agent judgment. Deterministic and identical every run.

-- B1: Match via matching_rules email lookup
UPDATE sightings SET
  person_id = (SELECT mr.person_id FROM matching_rules mr
    WHERE mr.identifier_type = 'email' AND mr.identifier_value = sightings.raw_email LIMIT 1),
  match_method = 'exact_email',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND raw_email IS NOT NULL
  AND raw_email IN (SELECT identifier_value FROM matching_rules WHERE identifier_type = 'email');

-- B1b: Fallback to people.email when no rule exists
UPDATE sightings SET
  person_id = (SELECT p.id FROM people p WHERE p.email = sightings.raw_email LIMIT 1),
  match_method = 'exact_email',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND raw_email IS NOT NULL
  AND raw_email IN (SELECT email FROM people);

-- B1b: Auto-create missing email rules for B1b matches
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT DISTINCT s.person_id, 'email', s.raw_email, s.source, 'high', 'Auto-created via B1b fallback'
FROM sightings s
WHERE s.person_id IS NOT NULL AND s.raw_email IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM matching_rules mr
    WHERE mr.identifier_type = 'email' AND mr.identifier_value = s.raw_email
  );

-- B2: Match via matching_rules slack_uid lookup
UPDATE sightings SET
  person_id = (SELECT mr.person_id FROM matching_rules mr
    WHERE mr.identifier_type = 'slack_uid' AND mr.identifier_value = sightings.source_uid LIMIT 1),
  match_method = 'exact_source_uid',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND source = 'slack'
  AND source_uid IN (SELECT identifier_value FROM matching_rules WHERE identifier_type = 'slack_uid');

-- B3: Match via matching_rules name_domain lookup
UPDATE sightings SET
  person_id = (SELECT mr.person_id FROM matching_rules mr
    WHERE mr.identifier_type = 'name_domain'
      AND mr.identifier_value = sightings.raw_name || '||' ||
        SUBSTR(sightings.raw_email, INSTR(sightings.raw_email, '@') + 1)
    LIMIT 1),
  match_method = 'fuzzy_name',
  match_confidence = (SELECT mr.confidence FROM matching_rules mr
    WHERE mr.identifier_type = 'name_domain'
      AND mr.identifier_value = sightings.raw_name || '||' ||
        SUBSTR(sightings.raw_email, INSTR(sightings.raw_email, '@') + 1)
    LIMIT 1),
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND raw_name IS NOT NULL AND raw_email LIKE '%@%'
  AND (raw_name || '||' || SUBSTR(raw_email, INSTR(raw_email, '@') + 1))
    IN (SELECT identifier_value FROM matching_rules WHERE identifier_type = 'name_domain');

-- Auto-create slack_uid rules for resolved Slack sightings that don't have one
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT DISTINCT s.person_id, 'slack_uid', s.source_uid, 'slack', 'high', 'Auto-created from Slack sighting'
FROM sightings s
WHERE s.source = 'slack' AND s.person_id IS NOT NULL AND s.source_uid IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM matching_rules mr
    WHERE mr.identifier_type = 'slack_uid' AND mr.identifier_value = s.source_uid
  );

-- B5: Create new people for still-unresolved sightings that have an email
INSERT INTO people (name, email, company, company_domain, first_seen, last_seen, sources, status, interaction_score)
SELECT
  s.raw_name,
  s.raw_email,
  s.raw_company,
  CASE WHEN s.raw_email LIKE '%@%'
    THEN SUBSTR(s.raw_email, INSTR(s.raw_email, '@') + 1) END,
  s.interaction_at,
  s.interaction_at,
  s.source,
  'new',
  CASE s.interaction_type
    WHEN 'meeting' THEN 3 WHEN 'email_sent' THEN 2 WHEN 'email_received' THEN 1
    WHEN 'slack_dm' THEN 2 WHEN 'slack_channel' THEN 1 ELSE 1 END
FROM sightings s
WHERE s.person_id IS NULL AND s.raw_email IS NOT NULL
  AND s.raw_email NOT IN (SELECT email FROM people WHERE email IS NOT NULL)
GROUP BY s.raw_email;

-- B5: Link sightings to newly created people
UPDATE sightings SET
  person_id = (SELECT p.id FROM people p WHERE p.email = sightings.raw_email LIMIT 1),
  match_method = 'exact_email',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND raw_email IS NOT NULL
  AND raw_email IN (SELECT email FROM people);

-- B5: Create initial email rules for new people
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT DISTINCT s.person_id, 'email', s.raw_email, s.source, 'high', 'Initial rule from first sighting'
FROM sightings s
WHERE s.person_id IS NOT NULL AND s.raw_email IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM matching_rules mr
    WHERE mr.identifier_type = 'email' AND mr.identifier_value = s.raw_email
  );

-- B5: Create initial slack_uid rules for new Slack people
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT DISTINCT s.person_id, 'slack_uid', s.source_uid, 'slack', 'high', 'Initial rule from first Slack sighting'
FROM sightings s
WHERE s.source = 'slack' AND s.person_id IS NOT NULL AND s.source_uid IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM matching_rules mr
    WHERE mr.identifier_type = 'slack_uid' AND mr.identifier_value = s.source_uid
  );

-- B5: Handle Slack-only contacts with no email (create person from source_uid)
INSERT INTO people (name, email, company, company_domain, first_seen, last_seen, sources, status, interaction_score)
SELECT
  s.raw_name,
  NULL,
  s.raw_company,
  NULL,
  s.interaction_at,
  s.interaction_at,
  'slack',
  'new',
  CASE s.interaction_type WHEN 'slack_dm' THEN 2 WHEN 'slack_channel' THEN 1 ELSE 1 END
FROM sightings s
WHERE s.person_id IS NULL AND s.raw_email IS NULL AND s.source = 'slack'
  AND s.source_uid NOT IN (
    SELECT identifier_value FROM matching_rules WHERE identifier_type = 'slack_uid'
  )
GROUP BY s.source_uid;

-- Link those Slack-only sightings
UPDATE sightings SET
  person_id = (SELECT mr.person_id FROM matching_rules mr
    WHERE mr.identifier_type = 'slack_uid' AND mr.identifier_value = sightings.source_uid LIMIT 1),
  match_method = 'exact_source_uid',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND source = 'slack' AND source_uid IS NOT NULL
  AND source_uid IN (SELECT identifier_value FROM matching_rules WHERE identifier_type = 'slack_uid');

-- For truly new Slack-only users, we need to create rules + link manually
-- Insert slack_uid rules for the people we just created by matching on name
INSERT OR IGNORE INTO matching_rules (person_id, identifier_type, identifier_value, source, confidence, notes)
SELECT p.id, 'slack_uid', s.source_uid, 'slack', 'high', 'Initial rule for Slack-only contact'
FROM sightings s
JOIN people p ON p.name = s.raw_name AND p.email IS NULL AND p.sources = 'slack'
WHERE s.person_id IS NULL AND s.source = 'slack' AND s.raw_email IS NULL
GROUP BY s.source_uid;

-- Final link for any remaining Slack-only
UPDATE sightings SET
  person_id = (SELECT mr.person_id FROM matching_rules mr
    WHERE mr.identifier_type = 'slack_uid' AND mr.identifier_value = sightings.source_uid LIMIT 1),
  match_method = 'exact_source_uid',
  match_confidence = 'high',
  matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE person_id IS NULL AND source = 'slack' AND source_uid IS NOT NULL
  AND source_uid IN (SELECT identifier_value FROM matching_rules WHERE identifier_type = 'slack_uid');

-- Auto-match linkedin_connections by email (zero MCP calls)
UPDATE people SET
  linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc
    WHERE lc.email = people.email AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high',
  status = 'connected',
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE linkedin_url IS NULL
  AND email IN (SELECT email FROM linkedin_connections WHERE linkedin_url IS NOT NULL AND linkedin_url != '');

-- Auto-match linkedin_connections by name
UPDATE people SET
  linkedin_url = (SELECT lc.linkedin_url FROM linkedin_connections lc
    WHERE LOWER(people.name) = LOWER(lc.first_name || ' ' || lc.last_name)
    AND lc.linkedin_url IS NOT NULL AND lc.linkedin_url != '' LIMIT 1),
  linkedin_confidence = 'high',
  status = 'connected',
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE linkedin_url IS NULL AND name LIKE '% %'
  AND LOWER(name) IN (SELECT LOWER(first_name || ' ' || last_name) FROM linkedin_connections
    WHERE linkedin_url IS NOT NULL AND linkedin_url != '');

-- Summary output
SELECT '=== Resolution Summary ===';
SELECT 'Resolved: ' || COUNT(*) FROM sightings WHERE person_id IS NOT NULL;
SELECT 'Unresolved (B4 candidates): ' || COUNT(*) FROM sightings WHERE person_id IS NULL;
SELECT 'Total matching rules: ' || COUNT(*) FROM matching_rules;
SELECT 'Auto-connected (from CSV): ' || COUNT(*) FROM people WHERE status = 'connected';
SELECT '';

-- B4 candidates: unresolved sightings with potential fuzzy matches in people
SELECT 'B4 candidates for agent review:';
SELECT s.id as sighting_id, s.raw_name, s.raw_email, s.source,
  p.id as candidate_person_id, p.name as candidate_name, p.email as candidate_email
FROM sightings s
LEFT JOIN people p ON p.company_domain = SUBSTR(s.raw_email, INSTR(s.raw_email, '@') + 1)
  AND (LOWER(p.name) LIKE '%' || LOWER(SUBSTR(s.raw_name, 1, INSTR(s.raw_name || ' ', ' ') - 1)) || '%')
WHERE s.person_id IS NULL AND p.id IS NOT NULL
LIMIT 20;
