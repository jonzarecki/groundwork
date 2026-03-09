-- update-people.sql
-- Phase D: Update all people who have sightings linked to them.
-- Run after resolve-sightings.sql: sqlite3 data/contacts.db < scripts/update-people.sql
-- Pure SQL, deterministic.

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
  interaction_score = COALESCE(
    (SELECT SUM(CASE interaction_type
      WHEN 'meeting' THEN 3
      WHEN 'email_sent' THEN 2
      WHEN 'email_received' THEN 1
      WHEN 'slack_dm' THEN 2
      WHEN 'slack_channel' THEN 1
      ELSE 0
    END) FROM sightings WHERE person_id = people.id),
    0),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id IN (SELECT DISTINCT person_id FROM sightings WHERE person_id IS NOT NULL);

SELECT 'Phase D: Updated ' || changes() || ' people';
