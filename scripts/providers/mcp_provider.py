"""MCP-based data collection provider.

Connects to the MCP proxy via SSE and calls Google Workspace + Slack MCP servers.
Requires: pip install mcp
"""
from __future__ import annotations

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
        "Or switch to the direct provider: --provider direct",
        file=sys.stderr,
    )
    raise

MCP_BASE = os.environ.get("MCP_PROXY_URL", "http://localhost:9090")
MAX_GMAIL_PAGES = int(os.environ.get("LC_MAX_GMAIL_PAGES", "3"))


def extract_text(result):
    """Extract text content from a CallToolResult."""
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    text = "\n".join(parts)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
    except (json.JSONDecodeError, TypeError):
        pass
    return text


async def collect_gmail(email, days):
    """Search Gmail and fetch metadata headers. Returns (text, page_count)."""
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

                page_token = None
                token_match = re.search(r"page_token='(\d+)'", search_text)
                if token_match:
                    page_token = token_match.group(1)
                if not page_token:
                    break

    return "\n---\n".join(all_text), len(all_text)


async def collect_calendar(email, days):
    """Get calendar events with attendee details. Returns text."""
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
                workspace = os.environ.get("LC_SLACK_WORKSPACE", "redhat")
                result = await session.read_resource(AnyUrl(f"slack://{workspace}/users"))
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


async def _resolve_slack_uid(session, email):
    """Look up the user's Slack UID by email or username."""
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
    """Search Slack for conversations involving me. Returns raw CSV."""
    url = f"{MCP_BASE}/slack/sse"
    date_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    async with sse_client(url, timeout=30, sse_read_timeout=120) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            my_uid = await _resolve_slack_uid(session, email)
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

                result = await session.call_tool("conversations_search_messages", args)
                text = extract_text(result)
                all_text.append(text)

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
    """Remove the Text column content from Slack CSV."""
    lines = csv_text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("MsgID,"):
            result.append(line)
            i += 1
            continue

        record = line
        while i + 1 < len(lines):
            ts_match = re.search(
                r",(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z),", record
            )
            if ts_match:
                break
            i += 1
            record += "\n" + lines[i]

        parts = record.split(",")
        if len(parts) >= 8:
            ts_idx = None
            for j in range(6, len(parts)):
                if re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", parts[j].strip().strip('"')):
                    ts_idx = j
                    break
            if ts_idx and ts_idx > 6:
                cleaned = ",".join(parts[:6]) + ",," + ",".join(parts[ts_idx:])
                result.append(cleaned)
            else:
                result.append(record.replace("\n", " "))
        else:
            result.append(record.replace("\n", " "))
        i += 1

    return "\n".join(result)


async def resolve_slack_cache_misses(slack_text, db_path):
    """Look up Slack users not in the cache. Returns (user_csv, count)."""
    import sqlite3 as sqlite3_mod

    cache = set()
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3_mod.connect(db_path)
            for row in conn.execute("SELECT slack_uid FROM slack_users"):
                cache.add(row[0])
            conn.close()
        except Exception:
            pass

    misses = {}
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
                                        (parts[0].strip(), parts[1].strip(),
                                         parts[2].strip() or None,
                                         parts[4].strip() or None,
                                         parts[5].strip() if len(parts) > 5 else None),
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
    """Look up names for emails that appeared without display names."""
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

        new_entries = {k: v for k, v in name_map.items() if k not in cache}
        if new_entries:
            _save_contact_names_cache(db_path, new_entries)

    mcp_found = len(name_map) - cache_hits
    if cache_hits or need_lookup:
        print(f"  Name cache: {cache_hits} hits, {len(need_lookup)} lookups ({mcp_found} found)", file=sys.stderr)

    return name_map


async def backfill_names(db_path, name_map):
    """Update people who have incomplete names using the directory name map."""
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
