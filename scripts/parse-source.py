#!/usr/bin/env python3
"""Parse MCP tool responses into sighting INSERT statements.

Usage:
    # Agent calls MCP, saves response to temp file, pipes to parser:
    python3 scripts/parse-source.py --source gmail --run-id 7 < /tmp/gmail_response.txt | sqlite3 data/contacts.db
    python3 scripts/parse-source.py --source calendar --run-id 7 < /tmp/calendar_response.txt | sqlite3 data/contacts.db
    python3 scripts/parse-source.py --source slack --run-id 7 < /tmp/slack_response.txt | sqlite3 data/contacts.db

Reads .env for LC_MAX_PARTICIPANTS and LC_SELF_EMAIL.
Outputs SQL INSERT statements to stdout.
"""

import argparse
import os
import re
import sys
from pathlib import Path


def load_env():
    env = {}
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Environment variables override .env file (useful for CI and tests)
    for key in ("LC_SELF_EMAIL", "LC_MAX_PARTICIPANTS", "LC_COLLECT_DAYS", "LC_PROVIDER"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def sql_escape(s):
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


SKIP_PATTERNS = [
    r"noreply@", r"no-reply@", r"comments-noreply@",
    r"notifications@", r"notification@",
    r"support@", r"jira-issues@", r"@.*\.atlassian\.net", r"notification@slack\.com",
    r"fridayfive@", r"announce-list@",
    r"-list@", r"-announce@", r"-all@", r"-team@", r"-sme@", r"-eng@",
    r"-managers@", r"-directs@", r"-leadership@", r"-specialists@",
    r"-devel@", r"-program@", r"-updates@", r"-docs@", r"-qe@",
    r"-bu@", r"-marketing@", r"-tooling@", r"-notes@",
    r"-strategy@", r"-platform@", r"-services@",
    r"@resource\.calendar\.google\.com", r"@group\.calendar\.google\.com",
    r"@googlegroups\.com",
]

SKIP_COMPILED = [re.compile(p, re.IGNORECASE) for p in SKIP_PATTERNS]

CALENDAR_INVITE_SUBJECTS = ["invitation:", "accepted:", "declined:", "updated:", "canceled:"]

GROUP_MEETING_THRESHOLD = 20
GROUP_WEIGHT = 5


def should_skip_email(email):
    if not email:
        return True
    for pat in SKIP_COMPILED:
        if pat.search(email):
            return True
    local = email.split("@")[0] if "@" in email else email
    if local.count("-") >= 2:
        return True
    return False


def extract_email_and_name(header_value):
    """Parse 'Name <email>' or just 'email' format."""
    m = re.match(r'^"?([^"<]*)"?\s*<([^>]+)>', header_value.strip())
    if m:
        return m.group(2).strip(), m.group(1).strip() or None
    email = header_value.strip().strip("<>")
    if "@" in email:
        return email, None
    return None, None


def company_from_email(email):
    if not email or "@" not in email:
        return None
    domain = email.split("@")[1].lower()
    domain_map = {
        "redhat.com": "Red Hat",
        "ibm.com": "IBM",
        "il.ibm.com": "IBM",
        "google.com": "Google",
        "microsoft.com": "Microsoft",
    }
    return domain_map.get(domain)


def parse_gmail(text, run_id, self_email, max_participants):
    """Parse get_gmail_messages_content_batch metadata output."""
    sightings = []
    blocks = re.split(r"\n---\n", text)

    for block in blocks:
        if "Message ID:" not in block:
            continue

        msg_id_m = re.search(r"Message ID:\s*(\S+)", block)
        subject_m = re.search(r"Subject:\s*(.+)", block)
        from_m = re.search(r"From:\s*(.+)", block)
        to_m = re.search(r"To:\s*(.+)", block)
        cc_m = re.search(r"Cc:\s*(.+)", block)
        date_m = re.search(r"Date:\s*(.+)", block)

        msg_id = msg_id_m.group(1) if msg_id_m else None
        subject = subject_m.group(1).strip() if subject_m else ""
        date_str = date_m.group(1).strip() if date_m else ""

        if any(subject.lower().startswith(p) for p in CALENDAR_INVITE_SUBJECTS):
            continue

        if "calendar-" in block and "@google.com" in block:
            mid_m = re.search(r"Message-ID:.*calendar-.*@google\.com", block)
            if mid_m:
                continue

        from_str = from_m.group(1).strip() if from_m else ""
        to_str = to_m.group(1).strip() if to_m else ""
        cc_str = cc_m.group(1).strip() if cc_m else ""

        all_addresses = []
        from_email, from_name = extract_email_and_name(from_str)
        is_sent_by_me = from_email and from_email.lower() == self_email.lower()

        for addr in re.split(r",(?=[^>]*(?:<|$))", to_str + "," + cc_str):
            addr = addr.strip()
            if not addr:
                continue
            email, name = extract_email_and_name(addr)
            if email:
                all_addresses.append((email, name))

        if from_email and not is_sent_by_me:
            all_addresses.append((from_email, from_name))

        total_participants = len(all_addresses) + (1 if is_sent_by_me else 0)
        if max_participants > 0 and total_participants > max_participants:
            continue

        list_unsub = re.search(r"List-Unsubscribe:\s*(.+)", block)
        precedence_m = re.search(r"Precedence:\s*(.+)", block)
        list_id = re.search(r"List-Id:\s*(.+)", block)
        is_mailing_list = bool(
            list_unsub or list_id
            or (precedence_m and precedence_m.group(1).strip().lower() in ("list", "bulk"))
        )

        for email, name in all_addresses:
            if email.lower() == self_email.lower():
                continue
            if should_skip_email(email):
                continue
            interaction_type = "email_sent" if is_sent_by_me else "email_received"
            context = subject[:100] if subject else None
            company = company_from_email(email)
            sightings.append({
                "run_id": run_id,
                "source": "gmail",
                "source_ref": msg_id,
                "source_uid": email,
                "raw_name": name,
                "raw_email": email,
                "raw_company": company,
                "raw_title": None,
                "raw_username": None,
                "interaction_type": interaction_type,
                "is_group": 1 if is_mailing_list else 0,
                "interaction_at": date_str,
                "context": context,
            })

    return sightings


def parse_calendar(text, run_id, self_email, max_participants):
    """Parse get_events detailed output."""
    sightings = []
    events = re.split(r'\n- "', text)

    for event in events:
        title_m = re.match(r'([^"]+)"', event)
        title = title_m.group(1).strip() if title_m else ""

        starts_m = re.search(r"Starts:\s*(\S+)", event)
        start_time = starts_m.group(1).rstrip(",") if starts_m else ""

        if not start_time or start_time.count("-") < 2:
            continue

        attendees_m = re.search(r"Attendees:\s*(.+?)(?:\n|$)", event)
        if not attendees_m or attendees_m.group(1).strip() == "None":
            continue

        attendee_str = attendees_m.group(1).strip()
        attendee_emails = re.findall(r"[\w.+-]+@[\w.-]+", attendee_str)

        if max_participants > 0 and len(attendee_emails) > max_participants:
            continue

        group_count = sum(1 for e in attendee_emails if should_skip_email(e))
        individual_count = len(attendee_emails) - group_count
        effective_count = individual_count + (group_count * GROUP_WEIGHT)
        is_large_meeting = effective_count > GROUP_MEETING_THRESHOLD

        event_id_m = re.search(r"ID:\s*(\S+)", event)
        event_id = event_id_m.group(1) if event_id_m else None

        email_to_name = {}
        for name, email_addr in re.findall(r"([\w\s.'-]+?)\s+<([\w.+-]+@[\w.-]+)>", attendee_str):
            name = name.strip().strip(",")
            if name and name[0].isupper():
                email_to_name[email_addr] = name

        for email in attendee_emails:
            if email.lower() == self_email.lower():
                continue
            if should_skip_email(email):
                continue
            name = email_to_name.get(email)
            company = company_from_email(email)
            sightings.append({
                "run_id": run_id,
                "source": "calendar",
                "source_ref": event_id,
                "source_uid": email,
                "raw_name": name,
                "raw_email": email,
                "raw_company": company,
                "raw_title": None,
                "raw_username": None,
                "interaction_type": "meeting",
                "is_group": 1 if is_large_meeting else 0,
                "interaction_at": start_time,
                "context": title[:100] if title else None,
            })

    return sightings


def parse_slack(text, run_id, self_email, max_participants):
    """Parse conversations_search_messages CSV output + users_search results.

    Expects format: one section of CSV message data, optionally followed by
    user lookup results (UserID,UserName,RealName,DisplayName,Email,Title,DMChannelID).
    The agent should concatenate the message CSV and user lookup CSV with a separator line '---'.
    """
    sightings = []
    self_uid = None

    sections = text.split("\n---\n") if "\n---\n" in text else [text]
    messages_text = sections[0]

    user_map = {}
    if len(sections) > 1:
        for line in sections[1].strip().splitlines():
            if line.startswith("UserID,") or not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 5:
                uid = parts[0].strip()
                user_map[uid] = {
                    "username": parts[1].strip(),
                    "real_name": parts[2].strip() or None,
                    "email": parts[4].strip() or None,
                    "title": parts[5].strip() if len(parts) > 5 else None,
                }

    seen_uids = set()
    for line in messages_text.strip().splitlines():
        if line.startswith("MsgID,") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 8:
            continue

        uid = parts[1].strip()
        username = parts[2].strip()
        real_name = parts[3].strip()
        channel = parts[4].strip()
        timestamp = parts[7].strip() if len(parts) > 7 else ""
        bot_name = parts[9].strip() if len(parts) > 9 else ""

        if bot_name:
            continue
        if not uid or uid == self_uid:
            continue
        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        user_info = user_map.get(uid, {})
        email = user_info.get("email")
        name = user_info.get("real_name") or real_name or None
        title = user_info.get("title")
        uname = user_info.get("username") or username or None

        if email and email.lower() == self_email.lower():
            self_uid = uid
            continue

        msg_id = parts[0].strip()
        is_dm = channel.startswith("#D") or channel.startswith("#mpdm-") or channel.startswith("#U")
        interaction_type = "slack_dm" if is_dm else "slack_channel"
        context = f"DM" if channel.startswith("#D") or channel.startswith("#U") else channel

        sightings.append({
            "run_id": run_id,
            "source": "slack",
            "source_ref": channel or msg_id,
            "source_uid": uid,
            "raw_name": name,
            "raw_email": email,
            "raw_company": company_from_email(email) if email else None,
            "raw_title": title,
            "raw_username": uname,
            "interaction_type": interaction_type,
            "is_group": 0 if is_dm else 1,
            "interaction_at": timestamp,
            "context": context[:100] if context else None,
        })

    return sightings


def parse_slack_with_cache(text, run_id, self_email, max_participants, db_path):
    """Parse Slack output using the slack_users cache.

    Supports two input formats:
    1. NEW (DM-first): channels_list output + conversations_history per channel
       Sections separated by '===CHANNEL <id>===' headers.
    2. LEGACY: conversations_search_messages CSV output

    Both may have user lookup results appended after a '---' separator.
    """
    cache = load_slack_cache(db_path)
    cache_hits = 0
    cache_misses = []

    # Split off user lookup section if present
    sections = text.split("\n---\n") if "\n---\n" in text else [text]
    messages_text = sections[0]

    agent_user_map = {}
    if len(sections) > 1:
        for line in sections[1].strip().splitlines():
            if line.startswith("UserID,") or not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 5:
                uid = parts[0].strip()
                agent_user_map[uid] = {
                    "username": parts[1].strip(),
                    "real_name": parts[2].strip() or None,
                    "email": parts[4].strip() or None,
                    "title": parts[5].strip() if len(parts) > 5 else None,
                }

    if agent_user_map:
        save_slack_cache(db_path, agent_user_map)
        cache.update(agent_user_map)

    # Detect format: new (has ===CHANNEL) or legacy (has MsgID, CSV header)
    if "===CHANNEL " in messages_text:
        sightings, cache_hits, cache_misses = _parse_slack_dm_history(
            messages_text, run_id, self_email, cache)
    else:
        sightings, cache_hits, cache_misses = _parse_slack_search_csv(
            messages_text, run_id, self_email, cache)

    if cache_misses:
        print(f"-- Slack cache: {cache_hits} hits, {len(cache_misses)} misses", file=sys.stderr)
        print(f"-- Cache misses (need users_search): {','.join(cache_misses)}", file=sys.stderr)
    else:
        print(f"-- Slack cache: {cache_hits} hits, 0 misses (all cached!)", file=sys.stderr)

    return sightings


def _parse_slack_dm_history(text, run_id, self_email, cache):
    """Parse channels_list + conversations_history format.

    Expected format:
    ===CHANNEL D12345 (im)===
    <conversations_history CSV output>
    ===CHANNEL G67890 (mpim)===
    <conversations_history CSV output>
    """
    sightings = []
    cache_hits = 0
    cache_misses = []
    seen_uids = set()
    self_uid = None

    channel_blocks = re.split(r"===CHANNEL\s+(\S+)\s+\((\w+)\)===", text)
    # channel_blocks: ['', channel_id, type, content, channel_id, type, content, ...]
    i = 1
    while i < len(channel_blocks) - 2:
        channel_id = channel_blocks[i].strip()
        channel_type = channel_blocks[i + 1].strip()
        content = channel_blocks[i + 2].strip()
        i += 3

        is_dm = channel_type in ("im", "mpim")
        interaction_type = "slack_dm" if is_dm else "slack_channel"
        context = "DM" if channel_type == "im" else "MPDM" if channel_type == "mpim" else channel_id

        for line in content.splitlines():
            if not line.strip() or line.startswith("MsgID,"):
                continue
            parts = line.split(",")
            if len(parts) < 8:
                continue

            uid = parts[1].strip()
            username = parts[2].strip()
            real_name = parts[3].strip()
            timestamp = parts[7].strip() if len(parts) > 7 else ""
            bot_name = parts[9].strip() if len(parts) > 9 else ""

            if bot_name:
                continue
            if not uid:
                continue

            user_info = cache.get(uid, {})
            email = user_info.get("email")
            if email and email.lower() == self_email.lower():
                self_uid = uid
                continue
            if uid == self_uid:
                continue

            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            if user_info:
                cache_hits += 1
            else:
                cache_misses.append(uid)

            name = user_info.get("real_name") or real_name or None
            title = user_info.get("title")
            uname = user_info.get("username") or username or None

            sightings.append({
                "run_id": run_id,
                "source": "slack",
                "source_ref": channel_id,
                "source_uid": uid,
                "raw_name": name,
                "raw_email": email,
                "raw_company": company_from_email(email) if email else None,
                "raw_title": title,
                "raw_username": uname,
                "interaction_type": interaction_type,
                "is_group": 0 if is_dm else 1,
                "interaction_at": timestamp,
                "context": context[:100] if context else None,
            })

    return sightings, cache_hits, cache_misses


def _parse_slack_search_csv(text, run_id, self_email, cache):
    """Parse legacy conversations_search_messages CSV format."""
    sightings = []
    cache_hits = 0
    cache_misses = []
    seen_uids = set()
    self_uid = None

    for line in text.strip().splitlines():
        if line.startswith("MsgID,") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 8:
            continue

        uid = parts[1].strip()
        username = parts[2].strip()
        real_name = parts[3].strip()
        channel = parts[4].strip()
        timestamp = parts[7].strip() if len(parts) > 7 else ""
        bot_name = parts[9].strip() if len(parts) > 9 else ""

        if bot_name:
            continue
        if not uid:
            continue

        user_info = cache.get(uid, {})
        email = user_info.get("email")
        if email and email.lower() == self_email.lower():
            self_uid = uid
            continue
        if uid == self_uid:
            continue

        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        if user_info:
            cache_hits += 1
        else:
            cache_misses.append(uid)

        name = user_info.get("real_name") or real_name or None
        title = user_info.get("title")
        uname = user_info.get("username") or username or None

        msg_id = parts[0].strip()
        is_dm = channel.startswith("#D") or channel.startswith("#mpdm-") or channel.startswith("#U")
        interaction_type = "slack_dm" if is_dm else "slack_channel"
        context = "DM" if channel.startswith("#D") or channel.startswith("#U") else channel

        sightings.append({
            "run_id": run_id,
            "source": "slack",
            "source_ref": channel or msg_id,
            "source_uid": uid,
            "raw_name": name,
            "raw_email": email,
            "raw_company": company_from_email(email) if email else None,
            "raw_title": title,
            "raw_username": uname,
            "interaction_type": interaction_type,
            "is_group": 0 if is_dm else 1,
            "interaction_at": timestamp,
            "context": context[:100] if context else None,
        })

    return sightings, cache_hits, cache_misses


def to_sql(sightings, dedup=True):
    """Convert sighting dicts to SQL INSERT statements.

    If dedup=True, wraps each INSERT so it skips if a sighting with the same
    source_ref + source_uid already exists (prevents duplicate sightings from
    overlapping collect runs).
    """
    cols = ("run_id, source, source_ref, source_uid, "
            "raw_name, raw_email, raw_company, raw_title, raw_username, "
            "interaction_type, is_group, interaction_at, context")
    lines = []
    for s in sightings:
        vals = (
            f"{s['run_id']}, {sql_escape(s['source'])}, {sql_escape(s['source_ref'])}, "
            f"{sql_escape(s['source_uid'])}, {sql_escape(s['raw_name'])}, "
            f"{sql_escape(s['raw_email'])}, {sql_escape(s['raw_company'])}, "
            f"{sql_escape(s['raw_title'])}, {sql_escape(s['raw_username'])}, "
            f"{sql_escape(s['interaction_type'])}, {s.get('is_group', 0)}, "
            f"{sql_escape(s['interaction_at'])}, {sql_escape(s['context'])}"
        )
        if dedup and s['source_ref'] and s['source_uid']:
            lines.append(
                f"INSERT INTO sightings ({cols}) "
                f"SELECT {vals} "
                f"WHERE NOT EXISTS (SELECT 1 FROM sightings "
                f"WHERE source_ref = {sql_escape(s['source_ref'])} "
                f"AND source_uid = {sql_escape(s['source_uid'])});"
            )
        else:
            lines.append(
                f"INSERT INTO sightings ({cols}) VALUES ({vals});"
            )
    return "\n".join(lines)


def load_slack_cache(db_path):
    """Load slack_users cache from the database."""
    cache = {}
    if not db_path or not Path(db_path).exists():
        return cache
    try:
        import sqlite3 as sqlite3_mod
        conn = sqlite3_mod.connect(db_path)
        for row in conn.execute("SELECT slack_uid, username, real_name, email, title FROM slack_users"):
            cache[row[0]] = {
                "username": row[1],
                "real_name": row[2],
                "email": row[3],
                "title": row[4],
            }
        conn.close()
    except Exception:
        pass
    return cache


def save_slack_cache(db_path, user_map):
    """Save new Slack user entries to the cache table."""
    if not db_path or not user_map:
        return
    try:
        import sqlite3 as sqlite3_mod
        conn = sqlite3_mod.connect(db_path)
        for uid, info in user_map.items():
            conn.execute(
                "INSERT OR REPLACE INTO slack_users (slack_uid, username, real_name, email, title) VALUES (?, ?, ?, ?, ?)",
                (uid, info.get("username"), info.get("real_name"), info.get("email"), info.get("title"))
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Parse MCP responses into sighting SQL")
    parser.add_argument("--source", required=True, choices=["gmail", "calendar", "slack"])
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--db-path", default=None, help="Path to contacts.db for slack_users cache")
    args = parser.parse_args()

    env = load_env()
    self_email = env.get("LC_SELF_EMAIL", "")
    if not self_email:
        print("Warning: LC_SELF_EMAIL not set in .env -- self-filtering disabled", file=sys.stderr)
    max_participants = int(env.get("LC_MAX_PARTICIPANTS", "80"))

    db_path = args.db_path
    if not db_path:
        default_db = Path(__file__).parent.parent / "data" / "contacts.db"
        if default_db.exists():
            db_path = str(default_db)

    text = sys.stdin.read()
    if not text.strip():
        print(f"-- No input for {args.source}", file=sys.stderr)
        sys.exit(0)

    if args.source == "slack":
        sightings = parse_slack_with_cache(text, args.run_id, self_email, max_participants, db_path)
    else:
        parsers = {
            "gmail": parse_gmail,
            "calendar": parse_calendar,
        }
        sightings = parsers[args.source](text, args.run_id, self_email, max_participants)

    print(f"-- Parsed {len(sightings)} sightings from {args.source}", file=sys.stderr)

    if sightings:
        print(to_sql(sightings))


if __name__ == "__main__":
    main()
