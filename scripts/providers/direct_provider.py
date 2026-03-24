"""Direct API data collection provider.

Uses Google OAuth + Slack browser cookie extraction. No Docker, no MCP, no proxy.

Requirements:
    pip install google-api-python-client google-auth-oauthlib pycookiecheat requests
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

CREDS_DIR = Path(__file__).parent.parent.parent / "data" / ".credentials"


def _load_google_creds():
    """Load Google OAuth credentials, refreshing if expired."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except ImportError:
        raise ImportError(
            "Google API client not installed. Run: pip install google-api-python-client google-auth-oauthlib"
        )

    token_file = CREDS_DIR / "google.json"
    if not token_file.exists():
        raise RuntimeError(
            "Google credentials not found.\n"
            "Run: python3 scripts/setup-auth.py google"
        )

    creds = Credentials.from_authorized_user_file(str(token_file))
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        token_file.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError(
            "Google credentials are invalid or expired.\n"
            "Run: python3 scripts/setup-auth.py google"
        )
    return creds


def _load_slack_creds():
    """Load cached Slack xoxc/xoxd tokens, re-extracting from Chrome if expired."""
    creds_file = CREDS_DIR / "slack.json"

    if creds_file.exists():
        creds = json.loads(creds_file.read_text())
        # Tokens are valid for ~14-30 days; check extracted_at
        extracted_at = creds.get("extracted_at", 0)
        age_days = (time.time() - extracted_at) / 86400
        if age_days < 13 and creds.get("xoxc") and creds.get("xoxd"):
            return creds["xoxc"], creds["xoxd"]
        print("  Slack tokens expired, re-extracting from Chrome...", file=sys.stderr)

    return _extract_slack_tokens()


def _extract_slack_tokens():
    """Extract xoxd from Chrome cookies and xoxc from the Slack web app."""
    try:
        from pycookiecheat import chrome_cookies
    except ImportError:
        raise ImportError(
            "pycookiecheat not installed. Run: pip install pycookiecheat"
        )

    workspace = os.environ.get("LC_SLACK_WORKSPACE", "")
    if not workspace:
        # Try to detect from saved creds
        creds_file = CREDS_DIR / "slack.json"
        if creds_file.exists():
            workspace = json.loads(creds_file.read_text()).get("workspace", "")
    if not workspace:
        raise RuntimeError(
            "LC_SLACK_WORKSPACE not set. Add it to .env (e.g. LC_SLACK_WORKSPACE=mycompany)\n"
            "Or run: python3 scripts/setup-auth.py slack"
        )

    slack_url = f"https://{workspace}.slack.com"
    print(f"  Extracting Slack cookies for {slack_url}...", file=sys.stderr)

    cookies = chrome_cookies(slack_url)
    xoxd = cookies.get("d", "")
    if not xoxd:
        raise RuntimeError(
            f"No 'd' cookie found for {slack_url}.\n"
            "Make sure you are logged into Slack in Chrome and try again."
        )

    # Extract xoxc from the boot_data embedded in the Slack web app
    import requests as _requests
    try:
        resp = _requests.get(slack_url, cookies={"d": xoxd}, timeout=15)
        xoxc_match = re.search(r'"api_token"\s*:\s*"(xoxc-[^"]+)"', resp.text)
        if not xoxc_match:
            # Alternative pattern
            xoxc_match = re.search(r"\"token\":\"(xoxc-[^\"]+)\"", resp.text)
        if not xoxc_match:
            raise RuntimeError(
                "Could not find xoxc token in Slack page. "
                "Try: python3 scripts/setup-auth.py slack --manual"
            )
        xoxc = xoxc_match.group(1)
    except _requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch Slack page for token extraction: {e}")

    # Cache the tokens
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    creds_data = {
        "xoxc": xoxc,
        "xoxd": xoxd,
        "workspace": workspace,
        "extracted_at": time.time(),
    }
    (CREDS_DIR / "slack.json").write_text(json.dumps(creds_data, indent=2))
    print(f"  Slack tokens cached to {CREDS_DIR / 'slack.json'}", file=sys.stderr)
    return xoxc, xoxd


def _slack_api(method, xoxc, xoxd, params=None, data=None, post=False):
    """Make a Slack Web API call with xoxc/xoxd auth."""
    import requests as _requests
    headers = {"Authorization": f"Bearer {xoxc}"}
    cookies = {"d": xoxd}
    url = f"https://slack.com/api/{method}"
    if post:
        resp = _requests.post(url, headers=headers, cookies=cookies, data=data or {}, timeout=30)
    else:
        resp = _requests.get(url, headers=headers, cookies=cookies, params=params or {}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {result.get('error', 'unknown')}")
    return result


# ---------------------------------------------------------------------------
# Gmail collection
# ---------------------------------------------------------------------------

MAX_GMAIL_PAGES = int(os.environ.get("LC_MAX_GMAIL_PAGES", "3"))


def _headers_to_dict(headers):
    """Convert Gmail API headers list to dict (last value wins)."""
    d = {}
    for h in headers:
        d[h["name"]] = h["value"]
    return d


def _msg_to_text_block(msg):
    """Format a Gmail API message (metadata format) as the text block parse-source.py expects."""
    headers = _headers_to_dict(msg.get("payload", {}).get("headers", []))
    parts = [f"Message ID: {msg['id']}"]
    for field in ("Subject", "From", "To", "Cc", "Date",
                  "List-Unsubscribe", "List-Id", "Precedence"):
        val = headers.get(field)
        if val:
            parts.append(f"{field}: {val}")
    return "\n".join(parts)


async def collect_gmail(email, days):
    """Collect Gmail using the Gmail API directly. Returns (text, page_count)."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API client not installed. Run: pip install google-api-python-client"
        )

    creds = _load_google_creds()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    query = (
        f"newer_than:{days}d"
        " -label:promotions -label:social -label:updates"
        " -from:jira-issues -from:noreply -from:notifications -from:no-reply"
    )

    METADATA_HEADERS = ["From", "To", "Cc", "Subject", "Date",
                        "List-Unsubscribe", "List-Id", "Precedence"]

    all_blocks = []
    page_token = None
    page_count = 0

    for _ in range(MAX_GMAIL_PAGES):
        list_kwargs = {"userId": "me", "q": query, "maxResults": 50}
        if page_token:
            list_kwargs["pageToken"] = page_token

        # Run synchronously in thread pool to avoid blocking event loop
        resp = await asyncio.to_thread(
            lambda kw=list_kwargs: service.users().messages().list(**kw).execute()
        )
        messages = resp.get("messages", [])
        if not messages:
            break

        # Batch fetch metadata
        for msg_stub in messages:
            msg = await asyncio.to_thread(
                lambda mid=msg_stub["id"]: service.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=METADATA_HEADERS
                ).execute()
            )
            block = _msg_to_text_block(msg)
            all_blocks.append(block)

        page_count += 1
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return "\n---\n".join(all_blocks), page_count


# ---------------------------------------------------------------------------
# Calendar collection
# ---------------------------------------------------------------------------

def _event_to_text(event):
    """Format a Calendar API event as text that parse-source.py expects."""
    title = event.get("summary", "(no title)")
    start = event.get("start", {})
    start_time = start.get("dateTime") or start.get("date", "")

    attendees = event.get("attendees", [])
    attendee_parts = []
    for a in attendees:
        email = a.get("email", "")
        name = a.get("displayName", "")
        if name:
            attendee_parts.append(f"{name} <{email}>")
        else:
            attendee_parts.append(email)

    attendee_str = ", ".join(attendee_parts) if attendee_parts else "None"
    event_id = event.get("id", "")

    return (
        f'- "{title}"\n'
        f"  Starts: {start_time}\n"
        f"  Attendees: {attendee_str}\n"
        f"  ID: {event_id}"
    )


async def collect_calendar(email, days):
    """Collect Calendar events using the Calendar API directly. Returns text."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API client not installed. Run: pip install google-api-python-client"
        )

    creds = _load_google_creds()
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    events_list = await asyncio.to_thread(
        lambda: service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=200,
        ).execute()
    )

    events = events_list.get("items", [])
    blocks = [_event_to_text(e) for e in events]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Slack collection
# ---------------------------------------------------------------------------

async def seed_slack_cache(db_path):
    """No-op for direct provider: Slack users are populated on demand."""
    return 0


async def collect_slack(days, email):
    """Collect Slack interactions using browser cookies + Web API.

    Uses the DM-history format (===CHANNEL ... ===) which parse-source.py understands.
    """
    xoxc, xoxd = _load_slack_creds()
    date_threshold = datetime.now(timezone.utc) - timedelta(days=days)
    oldest_ts = str(date_threshold.timestamp())

    # Get my UID
    identity = await asyncio.to_thread(
        lambda: _slack_api("auth.test", xoxc, xoxd)
    )
    my_uid = identity.get("user_id", "")

    # Get all DM and MPDM channels
    dm_channels = []
    cursor = ""
    while True:
        params = {
            "types": "im,mpim",
            "limit": 200,
            "exclude_archived": "true",
        }
        if cursor:
            params["cursor"] = cursor
        resp = await asyncio.to_thread(
            lambda p=params: _slack_api("conversations.list", xoxc, xoxd, params=p)
        )
        dm_channels.extend(resp.get("channels", []))
        next_cursor = resp.get("response_metadata", {}).get("next_cursor", "")
        if not next_cursor:
            break
        cursor = next_cursor

    sections = []
    csv_header = "MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,BotName,FileCount,AttachmentIDs,HasMedia"

    # Collect DM history
    for ch in dm_channels:
        ch_id = ch.get("id", "")
        ch_type = "im" if ch.get("is_im") else "mpim"

        # Get recent messages in this channel
        try:
            hist = await asyncio.to_thread(
                lambda cid=ch_id: _slack_api(
                    "conversations.history", xoxc, xoxd,
                    params={"channel": cid, "oldest": oldest_ts, "limit": 100}
                )
            )
        except Exception:
            continue

        messages = hist.get("messages", [])
        if not messages:
            continue

        rows = [csv_header]
        for m in messages:
            user_id = m.get("user", "")
            if not user_id or user_id == my_uid:
                continue
            if m.get("subtype") or m.get("bot_id"):
                continue
            ts = m.get("ts", "")
            dt_str = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ) if ts else ""
            thread_ts = m.get("thread_ts", "")
            row = f"{ts},{user_id},,, {ch_id},{thread_ts},,{dt_str},,,,,,"
            rows.append(row)

        if len(rows) > 1:
            sections.append(f"===CHANNEL {ch_id} ({ch_type})===\n" + "\n".join(rows))

    # Also search for messages from me (channel interactions)
    try:
        search_resp = await asyncio.to_thread(
            lambda: _slack_api(
                "search.messages", xoxc, xoxd, post=True,
                data={
                    "query": f"from:<@{my_uid}>",
                    "sort": "timestamp",
                    "sort_dir": "desc",
                    "count": 100,
                }
            )
        )
        matches = search_resp.get("messages", {}).get("matches", [])
        channel_msgs = {}  # channel_id -> [rows]
        for m in matches:
            ch_info = m.get("channel", {})
            ch_id = ch_info.get("id", "")
            ch_name = ch_info.get("name", ch_id)
            ch_type_name = "im" if ch_info.get("is_im") else "channel"
            if not ch_id or ch_type_name == "im":
                continue  # DMs already covered above
            ts = m.get("ts", "")
            dt_str = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ) if ts else ""
            user_id = m.get("user", my_uid)
            row = f"{ts},{user_id},,,#{ch_name},{ts},,{dt_str},,,,,,"
            channel_msgs.setdefault(ch_id, {"name": ch_name, "rows": [csv_header]})["rows"].append(row)

        for ch_id, ch_data in channel_msgs.items():
            if len(ch_data["rows"]) > 1:
                sections.append(
                    f"===CHANNEL #{ch_data['name']} (channel)===\n" + "\n".join(ch_data["rows"])
                )
    except Exception as e:
        print(f"  Slack search failed (skipping channel messages): {e}", file=sys.stderr)

    return "\n".join(sections)


async def resolve_slack_cache_misses(slack_text, db_path):
    """Look up unknown Slack users via users.info API. Returns (user_csv, count)."""
    import sqlite3 as sqlite3_mod

    # Find UIDs from the ===CHANNEL sections
    all_uids = set(re.findall(r"^[^,]+,(U[A-Z0-9]+),", slack_text, re.MULTILINE))
    if not all_uids:
        return "", 0

    # Check cache
    cached_uids = set()
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3_mod.connect(db_path)
            for row in conn.execute("SELECT slack_uid FROM slack_users"):
                cached_uids.add(row[0])
            conn.close()
        except Exception:
            pass

    misses = all_uids - cached_uids
    if not misses:
        return "", 0

    try:
        xoxc, xoxd = _load_slack_creds()
    except Exception as e:
        print(f"  Slack cache miss resolution skipped: {e}", file=sys.stderr)
        return "", 0

    resolved = []
    for uid in misses:
        try:
            info = await asyncio.to_thread(
                lambda u=uid: _slack_api("users.info", xoxc, xoxd, params={"user": u})
            )
            user = info.get("user", {})
            profile = user.get("profile", {})
            uname = user.get("name", "")
            real_name = profile.get("real_name", "")
            display_name = profile.get("display_name", "")
            user_email = profile.get("email", "")
            title = profile.get("title", "")
            dm_channel = ""
            row_data = {
                "username": uname,
                "real_name": real_name,
                "email": user_email,
                "title": title,
            }
            resolved.append(
                f"{uid},{uname},{real_name},{display_name},{user_email},{title},{dm_channel}"
            )
            # Save to cache
            if db_path and Path(db_path).exists():
                try:
                    conn = sqlite3_mod.connect(db_path)
                    conn.execute(
                        "INSERT OR REPLACE INTO slack_users (slack_uid, username, real_name, email, title) VALUES (?, ?, ?, ?, ?)",
                        (uid, uname, real_name or None, user_email or None, title or None),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
        except Exception as e:
            print(f"  users.info failed for {uid}: {e}", file=sys.stderr)

    if not resolved:
        return "", 0

    header = "UserID,UserName,RealName,DisplayName,Email,Title,DMChannelID"
    return header + "\n" + "\n".join(resolved), len(resolved)


# ---------------------------------------------------------------------------
# Name enrichment via Google People API
# ---------------------------------------------------------------------------

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


def _people_lookup(email_addr, people_service):
    """Look up a display name for an email via the People API (Workspace directory or contacts)."""
    # Try Workspace directory first (requires directory.readonly scope)
    try:
        results = people_service.people().searchDirectoryPeople(
            query=email_addr,
            readMask="names,emailAddresses",
            sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"],
            pageSize=1,
        ).execute()
        for person in results.get("people", []):
            names = person.get("names", [])
            if names:
                return names[0].get("displayName", "")
    except Exception:
        pass

    # Fallback: personal contacts
    try:
        results = people_service.people().searchContacts(
            query=email_addr,
            readMask="names,emailAddresses",
            pageSize=1,
        ).execute()
        for person in results.get("results", []):
            names = person.get("person", {}).get("names", [])
            if names:
                return names[0].get("displayName", "")
    except Exception:
        pass

    return ""


async def enrich_names_from_directory(gmail_text, calendar_text, db_path=None):
    """Look up display names for bare email addresses using the People API."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return {}

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
    need_lookup = []
    for email_addr in emails_without_names:
        if email_addr in cache:
            name_map[email_addr] = cache[email_addr]
        else:
            need_lookup.append(email_addr)

    cache_hits = len(name_map)

    if need_lookup:
        try:
            creds = _load_google_creds()
            people_service = build("people", "v1", credentials=creds, cache_discovery=False)
            for email_addr in need_lookup:
                name = await asyncio.to_thread(
                    lambda e=email_addr: _people_lookup(e, people_service)
                )
                if name and name != email_addr:
                    name_map[email_addr] = name
        except Exception as e:
            print(f"  People API enrichment failed: {e}", file=sys.stderr)

        new_entries = {k: v for k, v in name_map.items() if k not in cache}
        if new_entries:
            _save_contact_names_cache(db_path, new_entries)

    found = len(name_map) - cache_hits
    if cache_hits or need_lookup:
        print(f"  Name cache: {cache_hits} hits, {len(need_lookup)} lookups ({found} found)", file=sys.stderr)

    return name_map


async def backfill_names(db_path, name_map):
    """Update people with incomplete names using the People API."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return 0

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
        try:
            creds = _load_google_creds()
            people_service = build("people", "v1", credentials=creds, cache_discovery=False)
            new_names = {}
            for person_id, email_addr in need_lookup:
                name = await asyncio.to_thread(
                    lambda e=email_addr: _people_lookup(e, people_service)
                )
                if name and " " in name and name != email_addr:
                    new_names[email_addr] = name
        except Exception as e:
            print(f"  Backfill People API lookup failed: {e}", file=sys.stderr)
            new_names = {}

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
            print(f"  Backfill: {len(new_names)} names found via People API", file=sys.stderr)

    return updated
