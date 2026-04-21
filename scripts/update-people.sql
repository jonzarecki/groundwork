-- update-people.sql
-- Phase D: Update all people who have sightings linked to them.
-- Run after resolve-sightings.sql: sqlite3 data/contacts.db < scripts/update-people.sql
-- Pure SQL, deterministic.
--
-- Requires a temp table _home(d TEXT) with the user's home domain so that
-- is_external can be computed. Set it before running this script:
--   sqlite3 db -cmd "CREATE TEMP TABLE _home(d TEXT); INSERT INTO _home VALUES('redhat.com');" < update-people.sql
-- If _home is missing or empty, is_external is left NULL and no external bonus is applied.
--
-- Scoring formula (v5):
--
--   Interaction size is inferred from COUNT(DISTINCT source_uid) per source_ref.
--   For EXTERNAL contacts (company_domain != home_domain), large meetings are
--   treated as strong signal because cross-company meetings are deliberately
--   arranged -- the large size reflects that companies bring teams, not broadcast
--   dilution. The 5-person weak threshold only applies to INTERNAL meetings.
--
--   STRONG signal pool (uncapped, full weight per sighting):
--     1:1 meeting                       : 5 pts each   (1 other attendee)
--     small group meeting               : 4 pts each   (2-4 others, any domain)
--     cross-company large meeting       : 3 pts each   (5+ others, is_group=0, external person)
--     slack_dm                          : 4 pts each   (1:1 DM or MPIM, date-bucketed)
--     1:1 email_sent                    : 3 pts each
--     multi-recipient email_sent        : 2 pts each
--     1:1 email_received                : 2 pts each
--     multi-recipient email_received    : 1 pt  each
--
--   WEAK signal pool (linear: 1 pt per 3 distinct weak events, no hard cap):
--     internal large meeting (5+ others, is_group=0, internal person): counts toward pool
--     large meeting (is_group=1)                                      : counts toward pool
--     mailing list / group email (is_group=1)                         : counts toward pool
--     pool_score = total_weak_events / 3  (integer division)
--
--   has_direct_bonus: +5 if strong_direct_score > 0 (floor lift for any real contact)
--
--   external_direct_bonus: +10 if is_external=1 AND has_direct_score > 0
--     External contacts (outside your org domain) who you actually interacted
--     with directly are surfaced above equivalent internal weak-signal contacts.
--
--   channel_diversity: COUNT(DISTINCT interaction_type) among strong-signal sightings
--
--   div_multiplier (applied to strong_direct_score only):
--     diversity=1 → 1.0x
--     diversity=2 → 1.5x
--     diversity=3 → 2.5x
--     diversity=4+ → 4.0x
--
--   interaction_score = ROUND(strong_direct_score * div_multiplier)
--                     + weak_signal_points
--                     + has_direct_bonus
--                     + external_direct_bonus
--
-- Helper: a meeting is "weak" only if it is a large internal meeting.
-- Large EXTERNAL meetings (cross-company, is_group=0, 5+ others) are STRONG.
-- This expression evaluates to 1 when the meeting should go to the weak pool:
--   s.interaction_type = 'meeting'
--   AND 5+ attendees
--   AND (internal person OR unknown domain)

-- Safe fallback: create _home with no rows if not pre-populated by the caller.
-- (SELECT d FROM _home LIMIT 1) returns NULL, disabling is_external and the bonus.
CREATE TEMP TABLE IF NOT EXISTS _home(d TEXT);

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

  -- is_external: 1 if company_domain differs from home domain, 0 if same, NULL if unknown
  is_external = CASE
    WHEN people.company_domain IS NULL THEN NULL
    WHEN (SELECT d FROM _home LIMIT 1) IS NULL OR (SELECT d FROM _home LIMIT 1) = '' THEN NULL
    WHEN people.company_domain = (SELECT d FROM _home LIMIT 1) THEN 0
    ELSE 1
  END,

  -- channel_diversity: distinct strong-signal interaction types
  -- A meeting is strong if: is_group=0 AND (≤4 others OR external person)
  -- A meeting is weak only if: is_group=0 AND 5+ others AND internal/unknown person
  channel_diversity = COALESCE(
    (SELECT COUNT(DISTINCT s.interaction_type)
     FROM sightings s
     WHERE s.person_id = people.id
       AND s.is_group = 0
       AND NOT (
         -- Exclude only INTERNAL large meetings from strong/diversity count
         s.interaction_type = 'meeting'
         AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
              WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
         AND NOT (
           people.company_domain IS NOT NULL
           AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
           AND (SELECT d FROM _home LIMIT 1) != ''
           AND people.company_domain != (SELECT d FROM _home LIMIT 1)
         )
       )
    ), 0),

  interaction_score = (
    -- ── Strong direct score ──────────────────────────────────────────────────
    CAST(ROUND(
      COALESCE((
        -- 1:1 meetings (1 other sighting per event)
        SELECT SUM(5)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'meeting' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') = 1)
      ), 0)
      +
      COALESCE((
        -- Small group meetings (2-4 others, any domain)
        SELECT SUM(4)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'meeting' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') BETWEEN 2 AND 4)
      ), 0)
      +
      COALESCE((
        -- Cross-company large meetings (5+ others, is_group=0, external person)
        -- Companies bring teams; size reflects the relationship, not broadcast dilution.
        SELECT SUM(3)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'meeting' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
                AND people.company_domain IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) != ''
                AND people.company_domain != (SELECT d FROM _home LIMIT 1))
      ), 0)
      +
      COALESCE((
        -- Slack DMs (date-bucketed source_ref, each active day = 1 sighting)
        SELECT COUNT(*) * 4
        FROM sightings WHERE person_id = people.id AND interaction_type = 'slack_dm' AND is_group = 0
      ), 0)
      +
      COALESCE((
        -- 1:1 email_sent (1 other sighting for that message)
        SELECT SUM(3)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'email_sent' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'gmail') = 1)
      ), 0)
      +
      COALESCE((
        -- Multi-recipient email_sent (2+ others on the message)
        SELECT SUM(2)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'email_sent' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'gmail') > 1)
      ), 0)
      +
      COALESCE((
        -- 1:1 email_received (thread has 1 other person)
        -- source_ref = threadId for email_received, so count sightings per thread
        SELECT SUM(2)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'email_received' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'gmail') = 1)
      ), 0)
      +
      COALESCE((
        -- Multi-recipient email_received (thread has 2+ others)
        SELECT SUM(1)
        FROM (SELECT DISTINCT s.source_ref FROM sightings s
              WHERE s.person_id = people.id AND s.interaction_type = 'email_received' AND s.is_group = 0
                AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                     WHERE s2.source_ref = s.source_ref AND s2.source = 'gmail') > 1)
      ), 0)
    )
    -- Apply diversity multiplier to strong direct score
    * CASE
        WHEN COALESCE(
          (SELECT COUNT(DISTINCT s.interaction_type)
           FROM sightings s
           WHERE s.person_id = people.id
             AND s.is_group = 0
             AND NOT (
               s.interaction_type = 'meeting'
               AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                    WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
               AND NOT (
                 people.company_domain IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) != ''
                 AND people.company_domain != (SELECT d FROM _home LIMIT 1)
               )
             )
          ), 0) >= 4 THEN 4.0
        WHEN COALESCE(
          (SELECT COUNT(DISTINCT s.interaction_type)
           FROM sightings s
           WHERE s.person_id = people.id
             AND s.is_group = 0
             AND NOT (
               s.interaction_type = 'meeting'
               AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                    WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
               AND NOT (
                 people.company_domain IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) != ''
                 AND people.company_domain != (SELECT d FROM _home LIMIT 1)
               )
             )
          ), 0) = 3 THEN 2.5
        WHEN COALESCE(
          (SELECT COUNT(DISTINCT s.interaction_type)
           FROM sightings s
           WHERE s.person_id = people.id
             AND s.is_group = 0
             AND NOT (
               s.interaction_type = 'meeting'
               AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                    WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
               AND NOT (
                 people.company_domain IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                 AND (SELECT d FROM _home LIMIT 1) != ''
                 AND people.company_domain != (SELECT d FROM _home LIMIT 1)
               )
             )
          ), 0) = 2 THEN 1.5
        ELSE 1.0
      END
    AS INTEGER)

    -- ── Weak signal pool (linear: 1 pt per 3 distinct weak events, no hard cap) ──
    + (
        COALESCE((
          -- Internal large meetings only (5+ others, is_group=0, internal/unknown person)
          -- Cross-company large meetings are counted in the strong signal block above.
          SELECT COUNT(DISTINCT s.source_ref)
          FROM sightings s
          WHERE s.person_id = people.id AND s.interaction_type = 'meeting' AND s.is_group = 0
            AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                 WHERE s2.source_ref = s.source_ref AND s2.source = 'calendar') >= 5
            AND NOT (
              people.company_domain IS NOT NULL
              AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
              AND (SELECT d FROM _home LIMIT 1) != ''
              AND people.company_domain != (SELECT d FROM _home LIMIT 1)
            )
        ), 0)
        +
        COALESCE((
          -- Large meetings (is_group=1)
          SELECT COUNT(DISTINCT source_ref)
          FROM sightings WHERE person_id = people.id AND is_group = 1 AND interaction_type = 'meeting'
        ), 0)
        +
        COALESCE((
          -- Group/mailing-list email threads (is_group=1)
          SELECT COUNT(DISTINCT source_ref)
          FROM sightings WHERE person_id = people.id AND is_group = 1
            AND interaction_type IN ('email_received', 'email_sent')
        ), 0)
      ) / 3

    -- ── Direct contact bonus ─────────────────────────────────────────────────
    + CASE WHEN (
        COALESCE((
          SELECT COUNT(*) FROM sightings
          WHERE person_id = people.id
            AND interaction_type IN ('meeting', 'slack_dm', 'email_sent', 'email_received')
            AND is_group = 0
            AND NOT (
              -- Only exclude internal large meetings from direct bonus
              interaction_type = 'meeting'
              AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                   WHERE s2.source_ref = sightings.source_ref AND s2.source = 'calendar') >= 5
              AND NOT (
                people.company_domain IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) != ''
                AND people.company_domain != (SELECT d FROM _home LIMIT 1)
              )
            )
        ), 0) > 0
      ) THEN 5 ELSE 0 END

    -- ── External direct bonus ─────────────────────────────────────────────────
    -- +10 for external contacts (outside home domain) who have any direct interaction.
    -- Surfaces customers/partners above internal weak-signal contacts.
    + CASE WHEN (
        people.company_domain IS NOT NULL
        AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
        AND (SELECT d FROM _home LIMIT 1) != ''
        AND people.company_domain != (SELECT d FROM _home LIMIT 1)
        AND COALESCE((
          SELECT COUNT(*) FROM sightings
          WHERE person_id = people.id
            AND interaction_type IN ('meeting', 'slack_dm', 'email_sent', 'email_received')
            AND is_group = 0
            AND NOT (
              -- Only exclude internal large meetings from external bonus
              interaction_type = 'meeting'
              AND (SELECT COUNT(DISTINCT s2.source_uid) FROM sightings s2
                   WHERE s2.source_ref = sightings.source_ref AND s2.source = 'calendar') >= 5
              AND NOT (
                people.company_domain IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) IS NOT NULL
                AND (SELECT d FROM _home LIMIT 1) != ''
                AND people.company_domain != (SELECT d FROM _home LIMIT 1)
              )
            )
        ), 0) > 0
      ) THEN 10 ELSE 0 END
  ),

  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE id IN (SELECT DISTINCT person_id FROM sightings WHERE person_id IS NOT NULL);

SELECT 'Phase D: Updated ' || changes() || ' people';
