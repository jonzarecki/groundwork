-- update-people.sql
-- Phase D: Update all people who have sightings linked to them.
-- Run after resolve-sightings.sql: sqlite3 data/contacts.db < scripts/update-people.sql
-- Pure SQL, deterministic.
--
-- Scoring formula (v2, based on analysis of 122 actual LinkedIn connections):
--   direct_points  = meeting*3 + slack_dm*3 + email_sent*2 + email_received*1
--   group_points   = MIN(2, COUNT(DISTINCT group source_refs))
--   diversity      = COUNT(DISTINCT direct interaction types)
--   multi_channel_bonus = MAX(0, diversity - 1) * 3
--   interaction_score   = direct_points + group_points + multi_channel_bonus
--
-- Key insight: multi-channel interactions (meeting+slack) predict connections
-- far better than high single-channel counts (many received emails).

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
  channel_diversity = COALESCE(
    (SELECT COUNT(DISTINCT interaction_type)
     FROM sightings WHERE person_id = people.id AND is_group = 0), 0),
  interaction_score = (
    COALESCE((SELECT SUM(CASE interaction_type
      WHEN 'meeting' THEN 3
      WHEN 'slack_dm' THEN 3
      WHEN 'email_sent' THEN 2
      WHEN 'email_received' THEN 1
      ELSE 0
    END) FROM sightings WHERE person_id = people.id AND is_group = 0), 0)
    +
    MIN(2, COALESCE((SELECT COUNT(DISTINCT source_ref)
      FROM sightings WHERE person_id = people.id AND is_group = 1), 0))
    +
    MAX(0, COALESCE(
      (SELECT COUNT(DISTINCT interaction_type)
       FROM sightings WHERE person_id = people.id AND is_group = 0), 0) - 1) * 3
  ),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id IN (SELECT DISTINCT person_id FROM sightings WHERE person_id IS NOT NULL);

SELECT 'Phase D: Updated ' || changes() || ' people';
