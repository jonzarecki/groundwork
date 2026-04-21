# Scoring Intent

This file captures the **why** behind each design decision in the scoring system.
Update it whenever a scoring rule changes. The authoritative implementation is
`scripts/update-people.sql`. The summary in `CLAUDE.md` → "Scoring" should stay
in sync with both.

---

## Core philosophy

The score is a **relationship depth signal**, not a popularity or activity counter.
It answers: *"How much does this person know you personally?"*

Two contacts can both appear in 100 sightings. The one who sent you a 1:1 email
and had a 3-person call with you ranks higher than the one who was CC'd on 100
all-hands announcements. Channel diversity is a primary ranking factor — if someone
interacts with you across email, calendar, and Slack, that's a real relationship.

---

## Signal tiers

### Strong signal (full weight, uncapped)

These interactions require that someone specifically chose to interact with you,
not via a broadcast or list.

| Interaction | Weight | Rationale |
|---|---|---|
| 1:1 meeting | 5 pts | Most targeted — just you and them |
| Small group meeting (2-4 others) | 4 pts | Still intimate; everyone was individually invited |
| Cross-company large meeting (5+ others, external person) | 3 pts | Companies bring teams; size reflects the relationship, not dilution — see below |
| Slack DM (per active day) | 4 pts | Deliberate async reach-out; date-bucketed to avoid inflation |
| 1:1 email sent | 3 pts | You wrote to them specifically |
| Multi-recipient email sent | 2 pts | You included them intentionally, even if not alone |
| 1:1 email received (per thread) | 2 pts | They wrote to you specifically |
| Multi-recipient email received (per thread) | 1 pt | They included you, not alone |

### Weak signal pool (capped aggregate)

These are interactions where your presence was incidental — you received something
because of group membership, not because someone chose you specifically.

| Interaction | Rationale |
|---|---|
| Internal large meeting (5+ others, same org) | Could be an all-hands, town hall, or large team standup; your presence is structural |
| Large meeting (is_group=1) | Explicitly flagged as broadcast/list at parse time |
| Group/mailing-list email (is_group=1) | List headers present in the email; you were on a list, not addressed |

Weak events score **1 pt per 3 distinct events** (integer division, no cap).
This means weak-signal-only contacts max out around 3 pts with typical usage,
keeping them clearly below anyone you've had even one direct interaction with.

---

## Bonus intents

### has_direct_bonus (+5)

**Intent:** Create an uncrossable gap between "I've spoken with this person directly"
and "they've just been on things I was also on."

Any contact with `strong_direct_score > 0` gets +5. The minimum score for someone
you've had at least one real interaction with is 6 pts (1 pt weak → but they qualify
for has_direct_bonus → floor = 6). A contact with only weak signals stays at 0–3.

### external_direct_bonus (+10)

**Intent:** External contacts (customers, partners, prospects, community members)
who you've actually engaged with directly should surface *above* internal colleagues
you've barely interacted with.

Internal colleagues with weak-signal-only interactions score 0–3. An external contact
you've had one real interaction with gets 6 (direct) + 10 (external) = 16+ pts minimum.
This keeps external relationships visible even when sheer volume of internal traffic
would otherwise drown them out.

**Why only for contacts with direct interaction:** The external bonus is not a "this
person is interesting" flag — it's a "you actually engaged, don't let it get lost" flag.
External contacts you've only seen on CC'd threads or mass emails do NOT get the bonus.

---

## The cross-company meeting rule (v5, added Apr 2026)

**Problem:** A 13-person meeting between Red Hat and Samsung (7 Samsung + 6 RH) was
scoring 0 because the 5-attendee threshold classified it as a "medium group" weak event.

**Why the old rule was wrong for externals:** The medium-group threshold exists to filter
out internal all-hands meetings and org-wide invites where your presence is structural.
But a cross-company meeting is fundamentally different:
- Both sides chose to show up — it was a specific business interaction
- The large headcount is because companies bring teams (engineering, product, BD), not because it's a broadcast
- A 13-person customer meeting is more like a "small group meeting for each company" than an all-hands

**The fix:** For **external contacts** (`company_domain != home_domain`), any `is_group=0`
meeting counts as strong signal (3 pts), regardless of attendee count. The slight discount
(3 pts vs 4 pts for small group) acknowledges that you were one of several RH attendees —
it wasn't just you + them.

Internal large meetings (5+ others, same org) still go to the weak pool.

**Score impact for Samsung meeting example (13 attendees):**
- Before: 0 pts (weak pool, 1/3 = 0)
- After: 3 (cross-company meeting) + 5 (has_direct_bonus) + 10 (external_direct_bonus) = **18 pts**

---

## Diversity multiplier

**Intent:** Reward multi-channel relationships, which are strong evidence of a real
ongoing relationship vs. a single dense interaction.

| Distinct strong interaction types | Multiplier |
|---|---|
| 1 | 1.0× |
| 2 | 1.5× |
| 3 | 2.5× |
| 4+ | 4.0× |

Applied only to `strong_direct_score`, not to bonuses or weak pool.

"Distinct strong interaction types" = distinct values of `interaction_type` in strong-signal
sightings (meeting, slack_dm, email_sent, email_received). Internal large meetings are
excluded; cross-company large meetings count.

**Example:** Someone you've had 2 small-group meetings + 3 Slack DMs with:
- Strong score: 2×4 + 3×4 = 20, diversity = 2 types → 20 × 1.5 = 30
- Total: 30 + 5 (has_direct) + 10 (if external) = 45 pts

---

## Score tier guarantees

These are invariants — if they break, there's a bug.

| Profile | Minimum score |
|---|---|
| Weak-signal only (all-hands, mailing lists) | 0–3 pts |
| Any direct contact (internal) | 6+ pts |
| Any direct contact (external) | 16+ pts |
| Cross-company meeting (external) | 18+ pts |
| 1:1 or DM (external) | 19–20+ pts |
| Multi-channel ongoing relationship | 50–300+ pts |

---

## What is NOT scored

- **Email body content** — never stored, never scored (privacy)
- **LinkedIn connection status** — stored as `status=connected` but does not affect `interaction_score`; it's a separate dimension for review prioritization
- **LinkedIn enrichment results** — stored but not scored
- **Profile views, reactions, likes** — not collected
- **Meeting acceptance status** — everyone on a calendar invite is treated as attended

---

## Decisions NOT made (and why)

### Why not score by recency?

Recency decay would make the score unstable and hard to reason about. The viewer
shows `last_seen` explicitly — users can sort by recency themselves. Score measures
relationship depth, not freshness.

### Why not weight email sent vs received differently per company?

An external contact receiving your email or sending you one are both strong signals.
The directionality (sent/received) is already reflected in the point values. Adding
a domain-based weight on top would be too many variables.

### Why not give cross-company large meetings full small-group weight (4 pts)?

Slight discount to 3 pts acknowledges that you were one of N RH attendees — it wasn't
a 1-on-1 interaction between you and this person specifically. The 3 pts is enough to
trigger `has_direct_bonus` and `external_direct_bonus`, which is the main goal.
