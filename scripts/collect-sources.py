#!/usr/bin/env python3
"""Collect contacts from Gmail, Calendar, and Slack MCP servers.

Connects directly to the MCP proxy via SSE, makes all API calls
programmatically, and saves raw output to temp files for parse-source.py.

Zero agent tokens consumed -- this script replaces ~80 MCP tool calls.

Usage:
    python3 scripts/collect-sources.py [--days 7] [--email user@example.com] [--output-dir data/tmp]

Requires: pip install mcp (Python 3.10+)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from mcp.client.sse import sse_client
    from mcp import ClientSession
except ImportError:
    print(
        "Error: MCP Python SDK not found.\n"
        "Install it with: pip install mcp\n"
        "Or use conda Python: /path/to/conda/bin/python3 scripts/collect-sources.py",
        file=sys.stderr,
    )
    sys.exit(1)

MCP_BASE = os.environ.get("MCP_PROXY_URL", "http://localhost:9090")
MAX_GMAIL_PAGES = int(os.environ.get("LC_MAX_GMAIL_PAGES", "3"))


def load_env():
    env = {}
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def extract_text(result):
    """Extract text content from a CallToolResult."""
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    text = "\n".join(parts)
    # MCP tools often wrap output in {"result": "..."} JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
    except (json.JSONDecodeError, TypeError):
        pass
    return text


async def collect_gmail(email, days):
    """Search Gmail and fetch metadata headers. Returns raw text for parse-source.py."""
    url = f"{MCP_BASE}/google-workspace/sse"
    query = (
        f"newer_than:{days}d"
        " -label:promotions -label:social -label:updates"
        " -from:jira-issues -from:noreply -from:notifications -from:no-reply"
    )

    all_text = []
    async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            page_token = None
            for page in range(MAX_GMAIL_PAGES):
                search_args = {
                    "query": query,
                    "user_google_email": email,
                    "page_size": 25,
                }
                if page_token:
                    search_args["page_token"] = page_token

                search_result = await session.call_tool(
                    "search_gmail_messages", search_args
                )
                search_text = extract_text(search_result)

                # Extract message IDs from search results
                msg_ids = []
                for line in search_text.splitlines():
                    if "Message ID:" in line:
                        parts = line.split("Message ID:")
                        if len(parts) > 1:
                            msg_id = parts[1].strip()
                            if msg_id:
                                msg_ids.append(msg_id)

                if not msg_ids:
                    break

                # Fetch metadata for this page
                batch_result = await session.call_tool(
                    "get_gmail_messages_content_batch",
                    {
                        "message_ids": msg_ids,
                        "user_google_email": email,
                        "format": "metadata",
                    },
                )
                batch_text = extract_text(batch_result)
                all_text.append(batch_text)

                # Extract next page token from "page_token='12345'" format
                page_token = None
                token_match = re.search(r"page_token='(\d+)'", search_text)
                if token_match:
                    page_token = token_match.group(1)
                if not page_token:
                    break

    return "\n---\n".join(all_text), len(all_text)


async def collect_calendar(email, days):
    """Get calendar events with attendee details. Returns raw text for parse-source.py."""
    url = f"{MCP_BASE}/google-workspace/sse"
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "get_events",
                {
                    "user_google_email": email,
                    "time_min": time_min,
                    "time_max": time_max,
                    "max_results": 50,
                    "detailed": True,
                },
            )
            return extract_text(result)


async def seed_slack_cache(db_path):
    """Bulk-populate slack_users cache from the Slack directory resource if cache is small."""
    import sqlite3 as sqlite3_mod
    if not db_path or not Path(db_path).exists():
        return 0

    conn = sqlite3_mod.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM slack_users").fetchone()[0]
    conn.close()

    if count >= 100:
        return 0

    url = f"{MCP_BASE}/slack/sse"
    seeded = 0
    try:
        async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                from pydantic import AnyUrl
                result = await session.read_resource(AnyUrl("slack://redhat/users"))
                text = ""
                for content in result.contents:
                    if hasattr(content, "text"):
                        text += content.text

                conn = sqlite3_mod.connect(db_path)
                for line in text.strip().splitlines():
                    if line.startswith("UserID,") or not line.strip():
                        continue
                    parts = line.split(",")
                    if len(parts) >= 5:
                        uid = parts[0].strip()
                        username = parts[1].strip() if len(parts) > 1 else None
                        real_name = parts[2].strip() if len(parts) > 2 else None
                        email_addr = parts[4].strip() if len(parts) > 4 else None
                        title = parts[5].strip() if len(parts) > 5 else None
                        conn.execute(
                            "INSERT OR IGNORE INTO slack_users (slack_uid, username, real_name, email, title) VALUES (?, ?, ?, ?, ?)",
                            (uid, username, real_name, email_addr, title),
                        )
                        seeded += 1
                conn.commit()
                conn.close()
    except Exception as e:
        print(f"  Slack directory seed failed: {e}", file=sys.stderr)

    return seeded


async def resolve_slack_uid(session, email):
    """Look up the user's Slack UID by email or username."""
    # Try by email first, then by username prefix
    for query in [email, email.split("@")[0]]:
        result = await session.call_tool("users_search", {"query": query})
        text = extract_text(result)
        for line in text.strip().splitlines():
            if line.startswith("UserID,") or not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 5:
                return parts[0].strip()
    return None


async def collect_slack(days, email):
    """Search Slack for conversations involving me. Returns raw CSV for parse-source.py."""
    url = f"{MCP_BASE}/slack/sse"
    date_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Resolve Slack UID (filter_users_with="@me" doesn't work)
            my_uid = await resolve_slack_uid(session, email)
            if not my_uid:
                return "MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,BotName,FileCount,AttachmentIDs,HasMedia,Cursor\n"

            all_text = []
            cursor = ""

            while True:
                args = {
                    "filter_users_with": my_uid,
                    "filter_date_after": date_after,
                    "limit": 100,
                }
                if cursor:
                    args["cursor"] = cursor

                result = await session.call_tool(
                    "conversations_search_messages", args
                )
                text = extract_text(result)
                all_text.append(text)

                # Check for pagination cursor in last line
                lines = text.strip().splitlines()
                if lines:
                    last_parts = lines[-1].split(",")
                    if len(last_parts) > 13 and last_parts[-1].strip():
                        cursor = last_parts[-1].strip()
                        continue
                break

    combined = "\n".join(all_text)
    return _strip_slack_text_column(combined)


def _strip_slack_text_column(csv_text):
    """Remove the Text column content from Slack CSV to prevent comma/newline breakage.

    The Slack MCP returns unquoted CSV where the Text field (column 6) can contain
    commas and newlines, breaking simple comma-split parsing. Since we only need
    metadata (UserID, RealName, Channel, Time), strip the text content.
    """
    lines = csv_text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("MsgID,"):
            result.append(line)
            i += 1
            continue

        # Accumulate lines until we find the timestamp pattern that ends a record
        record = line
        while i + 1 < len(lines):
            # Check if the current accumulated record contains a valid timestamp
            # Format: ...,2026-03-13T05:17:22Z,...
            ts_match = re.search(
                r",(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z),", record
            )
            if ts_match:
                break
            i += 1
            record += "\n" + lines[i]

        # Parse the record: fields before Text are simple (no commas)
        # MsgID(0), UserID(1), UserName(2), RealName(3), Channel(4), ThreadTs(5), Text(6...), Time, ...
        parts = record.split(",")
        if len(parts) >= 8:
            # Find the timestamp field to locate where Text ends
            ts_idx = None
            for j in range(6, len(parts)):
                if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", parts[j].strip().strip('"')):
                    ts_idx = j
                    break
            if ts_idx and ts_idx > 6:
                # Rebuild with empty Text field
                cleaned = ",".join(parts[:6]) + ",," + ",".join(parts[ts_idx:])
                result.append(cleaned)
            else:
                result.append(record.replace("\n", " "))
        else:
            result.append(record.replace("\n", " "))
        i += 1

    return "\n".join(result)


async def resolve_slack_cache_misses(slack_text, db_path):
    """Look up Slack users not in the cache. Returns user lookup CSV to append after ---."""
    import sqlite3 as sqlite3_mod

    # Load existing cache
    cache = set()
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3_mod.connect(db_path)
            for row in conn.execute("SELECT slack_uid FROM slack_users"):
                cache.add(row[0])
            conn.close()
        except Exception:
            pass

    # Find UIDs, usernames, and real names from the Slack CSV that aren't cached
    misses = {}  # uid -> (username, real_name)
    for line in slack_text.strip().splitlines():
        if line.startswith("MsgID,") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) >= 4:
            uid = parts[1].strip()
            username = parts[2].strip()
            real_name = parts[3].strip() if len(parts) > 3 else ""
            if uid and uid not in cache and uid not in misses:
                misses[uid] = (username, real_name)

    if not misses:
        return "", 0

    url = f"{MCP_BASE}/slack/sse"
    resolved = []
    async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            for uid, (username, real_name) in misses.items():
                found = False
                # Try username first, then real_name as fallback
                for query in [username, real_name]:
                    if not query:
                        continue
                    result = await session.call_tool("users_search", {"query": query})
                    text = extract_text(result)
                    for line in text.strip().splitlines():
                        if line.startswith("UserID,") or not line.strip():
                            continue
                        parts = line.split(",")
                        if len(parts) >= 5 and parts[0].strip() == uid:
                            resolved.append(line)
                            if db_path and Path(db_path).exists():
                                try:
                                    conn = sqlite3_mod.connect(db_path)
                                    conn.execute(
                                        "INSERT OR REPLACE INTO slack_users (slack_uid, username, real_name, email, title) VALUES (?, ?, ?, ?, ?)",
                                        (parts[0].strip(), parts[1].strip(), parts[2].strip() or None,
                                         parts[4].strip() or None, parts[5].strip() if len(parts) > 5 else None),
                                    )
                                    conn.commit()
                                    conn.close()
                                except Exception:
                                    pass
                            found = True
                            break
                    if found:
                        break

    if not resolved:
        return "", 0

    header = "UserID,UserName,RealName,DisplayName,Email,Title,DMChannelID"
    return header + "\n" + "\n".join(resolved), len(resolved)


def _load_contact_names_cache(db_path):
    """Load cached email->name mappings from the contact_names table."""
    cache = {}
    if not db_path or not Path(db_path).exists():
        return cache
    try:
        import sqlite3 as sqlite3_mod
        conn = sqlite3_mod.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_names (
                email TEXT PRIMARY KEY,
                name TEXT,
                title TEXT,
                fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        conn.commit()
        for row in conn.execute("SELECT email, name FROM contact_names WHERE name IS NOT NULL"):
            cache[row[0]] = row[1]
        conn.close()
    except Exception:
        pass
    return cache


def _save_contact_names_cache(db_path, name_map):
    """Save email->name mappings to the contact_names cache table."""
    if not db_path or not name_map:
        return
    try:
        import sqlite3 as sqlite3_mod
        conn = sqlite3_mod.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_names (
                email TEXT PRIMARY KEY,
                name TEXT,
                title TEXT,
                fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        for email_addr, name in name_map.items():
            conn.execute(
                "INSERT OR REPLACE INTO contact_names (email, name) VALUES (?, ?)",
                (email_addr, name),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


async def enrich_names_from_directory(gmail_text, calendar_text, db_path=None):
    """Look up names for emails that appeared without display names in Gmail/Calendar.

    Uses a contact_names cache table to avoid redundant lookups on subsequent runs.
    """
    # Load cache
    cache = _load_contact_names_cache(db_path) if db_path else {}

    emails_without_names = set()

    for block in re.split(r"\n---\n", gmail_text):
        from_m = re.search(r"From:\s*(.+)", block)
        if from_m:
            val = from_m.group(1).strip()
            if "@" in val and "<" not in val:
                emails_without_names.add(val.strip().strip("<>"))

        for header in ("To:", "Cc:"):
            h_m = re.search(rf"{header}\s*(.+)", block)
            if h_m:
                for addr in re.split(r",(?=[^>]*(?:<|$))", h_m.group(1)):
                    addr = addr.strip()
                    if "@" in addr and "<" not in addr:
                        emails_without_names.add(addr.strip().strip("<>"))

    for email in re.findall(r"[\w.+-]+@[\w.-]+", calendar_text):
        emails_without_names.add(email)

    if not emails_without_names:
        return {}

    # Resolve from cache first
    name_map = {}
    need_lookup = set()
    for email_addr in emails_without_names:
        if email_addr in cache:
            name_map[email_addr] = cache[email_addr]
        else:
            need_lookup.add(email_addr)

    cache_hits = len(name_map)

    if need_lookup:
        url = f"{MCP_BASE}/google-contacts/sse"
        try:
            async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    for email_addr in need_lookup:
                        result = await session.call_tool(
                            "search_directory", {"query": email_addr, "max_results": 1}
                        )
                        text = extract_text(result)
                        name_m = re.search(r"Name:\s*(.+)", text)
                        if name_m:
                            name = name_m.group(1).strip()
                            if name and name != email_addr:
                                name_map[email_addr] = name
        except Exception as e:
            print(f"  Google Contacts enrichment failed: {e}", file=sys.stderr)

        # Save new lookups to cache
        new_entries = {k: v for k, v in name_map.items() if k not in cache}
        if new_entries:
            _save_contact_names_cache(db_path, new_entries)

    mcp_found = len(name_map) - cache_hits
    if cache_hits or need_lookup:
        print(f"  Name cache: {cache_hits} hits, {len(need_lookup)} lookups ({mcp_found} found)", file=sys.stderr)

    return name_map


def inject_names_into_gmail(gmail_text, name_map):
    """Replace bare email addresses in Gmail metadata with 'Name <email>' format."""
    if not name_map:
        return gmail_text

    result = gmail_text
    for email_addr, name in name_map.items():
        result = result.replace(f"From: {email_addr}", f'From: {name} <{email_addr}>')
        result = result.replace(f" {email_addr},", f' {name} <{email_addr}>,')
        result = result.replace(f" {email_addr}\n", f' {name} <{email_addr}>\n')
        result = result.replace(f",{email_addr}", f',{name} <{email_addr}>')
    return result


def inject_names_into_calendar(calendar_text, name_map):
    """Replace bare emails in Calendar Attendees lines with 'Name <email>' format.

    Transforms: Attendees: user@corp.com, other@corp.com
    Into:        Attendees: John Doe <user@corp.com>, Jane Smith <other@corp.com>
    """
    if not name_map:
        return calendar_text

    result = calendar_text
    for email_addr, name in name_map.items():
        # In Attendees line: "email," or "email\n"
        result = result.replace(f" {email_addr},", f' {name} <{email_addr}>,')
        result = result.replace(f" {email_addr}\n", f' {name} <{email_addr}>\n')
        # At start of Attendees list
        result = result.replace(f"Attendees: {email_addr},", f'Attendees: {name} <{email_addr}>,')
        result = result.replace(f"Attendees: {email_addr}\n", f'Attendees: {name} <{email_addr}>\n')
    return result


async def _backfill_names(db_path, name_map):
    """Update people who have incomplete names (single-word) using the directory name map.

    First checks the name_map from the current run. For remaining misses,
    queries Google Contacts directly and updates the contact_names cache.
    """
    import sqlite3 as sqlite3_mod
    conn = sqlite3_mod.connect(db_path)
    updated = 0
    rows = conn.execute(
        "SELECT id, email, name FROM people WHERE name NOT LIKE '% %' AND email IS NOT NULL AND status != 'ignored'"
    ).fetchall()

    need_lookup = []
    for person_id, email_addr, current_name in rows:
        if email_addr in name_map and " " in name_map[email_addr]:
            conn.execute(
                "UPDATE people SET name = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (name_map[email_addr], person_id),
            )
            updated += 1
        else:
            need_lookup.append((person_id, email_addr))

    conn.commit()
    conn.close()

    if need_lookup:
        url = f"{MCP_BASE}/google-contacts/sse"
        new_names = {}
        try:
            async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for person_id, email_addr in need_lookup:
                        result = await session.call_tool(
                            "search_directory", {"query": email_addr, "max_results": 1}
                        )
                        text = extract_text(result)
                        name_m = re.search(r"Name:\s*(.+)", text)
                        if name_m:
                            name = name_m.group(1).strip()
                            if name and " " in name and name != email_addr:
                                new_names[email_addr] = name
        except Exception as e:
            print(f"  Backfill directory lookup failed: {e}", file=sys.stderr)

        if new_names:
            conn = sqlite3_mod.connect(db_path)
            for email_addr, name in new_names.items():
                conn.execute(
                    "UPDATE people SET name = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE email = ?",
                    (name, email_addr),
                )
            conn.commit()
            conn.close()
            _save_contact_names_cache(db_path, new_names)
            updated += len(new_names)
            print(f"  Backfill: {len(new_names)} names found via directory lookup", file=sys.stderr)

    return updated


async def main_async(email, days, output_dir):
    """Collect from all sources, save to files, print summary."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    db_path = str(Path(__file__).parent.parent / "data" / "contacts.db")

    gmail_file = output / "lc_gmail.txt"
    calendar_file = output / "lc_calendar.txt"
    slack_file = output / "lc_slack.txt"

    results = {}
    errors = {}
    gmail_text = ""
    calendar_text = ""

    # Gmail
    print(f"Collecting Gmail (last {days}d, max {MAX_GMAIL_PAGES} pages)...", file=sys.stderr)
    try:
        gmail_text, page_count = await collect_gmail(email, days)
        results["gmail"] = f"{page_count} pages"
    except Exception as e:
        errors["gmail"] = str(e)
        results["gmail"] = f"FAILED: {e}"

    # Calendar
    print(f"Collecting Calendar (last {days}d)...", file=sys.stderr)
    try:
        calendar_text = await collect_calendar(email, days)
        results["calendar"] = "ok"
    except Exception as e:
        errors["calendar"] = str(e)
        results["calendar"] = f"FAILED: {e}"

    # Seed Slack cache if needed
    slack_seeded = await seed_slack_cache(db_path)
    if slack_seeded:
        print(f"  Slack directory seeded: {slack_seeded} users", file=sys.stderr)

    # Slack
    print(f"Collecting Slack (last {days}d)...", file=sys.stderr)
    slack_text = ""
    try:
        slack_text = await collect_slack(days, email)
        results["slack"] = "ok"
    except Exception as e:
        errors["slack"] = str(e)
        results["slack"] = f"FAILED: {e}"

    # Enrich: resolve Slack cache misses
    slack_resolved = 0
    if slack_text:
        print("Resolving Slack user cache misses...", file=sys.stderr)
        try:
            user_csv, slack_resolved = await resolve_slack_cache_misses(slack_text, db_path)
            if user_csv:
                slack_text = slack_text + "\n---\n" + user_csv
        except Exception as e:
            print(f"  Slack cache resolution failed: {e}", file=sys.stderr)

    # Enrich: look up names from Google Contacts directory
    name_map = {}
    if gmail_text or calendar_text:
        print("Enriching names from Google Contacts directory...", file=sys.stderr)
        try:
            name_map = await enrich_names_from_directory(gmail_text, calendar_text, db_path)
            if name_map:
                gmail_text = inject_names_into_gmail(gmail_text, name_map)
                calendar_text = inject_names_into_calendar(calendar_text, name_map)
        except Exception as e:
            print(f"  Name enrichment failed: {e}", file=sys.stderr)

    # Backfill: update people with incomplete names from directory
    backfilled = 0
    if name_map and Path(db_path).exists():
        print("Backfilling incomplete names in DB...", file=sys.stderr)
        try:
            backfilled = await _backfill_names(db_path, name_map)
        except Exception as e:
            print(f"  Name backfill failed: {e}", file=sys.stderr)

    # Write final output files
    gmail_file.write_text(gmail_text)
    calendar_file.write_text(calendar_text)
    slack_file.write_text(slack_text)

    # Structured summary to stdout
    gmail_size = gmail_file.stat().st_size
    cal_size = calendar_file.stat().st_size
    slack_size = slack_file.stat().st_size

    print(f"Gmail: {results['gmail']} -> {gmail_file} ({gmail_size:,}B)")
    print(f"Calendar: {results['calendar']} -> {calendar_file} ({cal_size:,}B)")
    print(f"Slack: {results['slack']} -> {slack_file} ({slack_size:,}B)")
    if slack_resolved:
        print(f"Slack users resolved: {slack_resolved}")
    if name_map:
        print(f"Names from directory: {len(name_map)}")
    if backfilled:
        print(f"Names backfilled in DB: {backfilled}")

    if errors:
        print(f"ERRORS: {', '.join(errors.keys())}")
        return 1
    return 0


def main():
    env = load_env()

    parser = argparse.ArgumentParser(
        description="Collect contacts from MCP sources (Gmail, Calendar, Slack)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(env.get("LC_COLLECT_DAYS", "7")),
        help="Collection window in days (default: LC_COLLECT_DAYS or 7)",
    )
    parser.add_argument(
        "--email",
        default=env.get("LC_SELF_EMAIL", ""),
        help="Your email address (default: LC_SELF_EMAIL from .env)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent.parent / "data" / "tmp"),
        help="Directory for temp files (default: data/tmp)",
    )
    args = parser.parse_args()

    if not args.email:
        print("Error: --email required or set LC_SELF_EMAIL in .env", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(main_async(args.email, args.days, args.output_dir))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
