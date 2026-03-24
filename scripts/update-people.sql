-- update-people.sql
-- Phase D: Update all people who have sightings linked to them.
-- Run after resolve-sightings.sql: sqlite3 data/contacts.db < scripts/update-people.sql
-- Pure SQL, deterministic.
--
-- Scoring: direct interactions get full weight; group interactions (mailing lists,
-- large meetings, slack channels) get 1 point per unique thread/event, capped at 3.
-- channel_diversity = count of distinct direct interaction types (strong signal).

UPDATE people SET
  last_seen = COALESCE(
    (SELECT MAX(interaction_at) FROM sightings WHERE person_id = people.id),
    last_seen),
  name = COALESCE(
    (SELECT s.raw_name FROM sightings s
     WHERE s.person_id = people.id AND s.raw_name LIKE '% %'
     ORDER BY s.interaction_at DESC LIMIT 1),
    name),
  sources = COALESCE(
    (SELECT GROUP_CONCAT(DISTINCT source) FROM sightings WHERE person_id = people.id),
    sources),
  interaction_score = (
    -- Direct interactions: full weight per sighting
    COALESCE((SELECT SUM(CASE interaction_type
      WHEN 'meeting' THEN 3
      WHEN 'email_sent' THEN 2
      WHEN 'email_received' THEN 1
      WHEN 'slack_dm' THEN 2
      ELSE 0
    END) FROM sightings WHERE person_id = people.id AND is_group = 0), 0)
    +
    -- Group interactions: 1 point per unique event/thread, max 3
    MIN(3, COALESCE((SELECT COUNT(DISTINCT source_ref)
      FROM sightings WHERE person_id = people.id AND is_group = 1), 0))
  ),
  channel_diversity = COALESCE(
    (SELECT COUNT(DISTINCT interaction_type)
     FROM sightings WHERE person_id = people.id AND is_group = 0), 0),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id IN (SELECT DISTINCT person_id FROM sightings WHERE person_id IS NOT NULL);

SELECT 'Phase D: Updated ' || changes() || ' people';
