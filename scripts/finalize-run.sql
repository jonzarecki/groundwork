-- finalize-run.sql
-- Updates the most recent open run with contact counts and finish time.
-- Run at the end of a collect: sqlite3 data/contacts.db < scripts/finalize-run.sql

UPDATE runs SET
  finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
  contacts_found = (SELECT COUNT(*) FROM sightings WHERE run_id = runs.id),
  contacts_new = (SELECT COUNT(DISTINCT p.id) FROM people p
    JOIN sightings s ON s.person_id = p.id
    WHERE s.run_id = runs.id
      AND p.id NOT IN (SELECT DISTINCT person_id FROM sightings WHERE run_id != runs.id AND person_id IS NOT NULL)),
  contacts_updated = (SELECT COUNT(DISTINCT person_id) FROM sightings WHERE run_id = runs.id AND person_id IS NOT NULL)
WHERE id = (SELECT MAX(id) FROM runs WHERE finished_at IS NULL);

SELECT '=== Run Finalized ===';
SELECT 'Run ID:    ' || id FROM runs WHERE id = (SELECT MAX(id) FROM runs);
SELECT 'Sightings: ' || contacts_found FROM runs WHERE id = (SELECT MAX(id) FROM runs);
SELECT 'New:       ' || contacts_new FROM runs WHERE id = (SELECT MAX(id) FROM runs);
SELECT 'Updated:   ' || contacts_updated FROM runs WHERE id = (SELECT MAX(id) FROM runs);
SELECT 'People:    ' || COUNT(*) FROM people;
SELECT 'Rules:     ' || COUNT(*) FROM matching_rules;
