#!/usr/bin/env python3
"""Import contacts from exported data files (offline/no-API mode).

Supports:
  - Gmail: Google Takeout mbox files (.mbox)
  - Calendar: iCalendar files (.ics)
  - Slack: Slack workspace export JSON (channels/ directory or ZIP)
  - LinkedIn: Connections.csv (already supported by import-connections.sh)

Reads files from data/imports/ and writes the same intermediate text format
that parse-source.py consumes, then runs the processing pipeline.

Usage:
    # 1. Drop export files into data/imports/
    #    - Gmail.mbox (from Google Takeout)
    #    - Calendar.ics (from Google Calendar > Settings > Export)
    #    - slack_export/  or  slack_export.zip  (from Slack admin)
    # 2. Run:
    python3 scripts/import-sources.py [--run-id 0] [--output-dir data/tmp]

Requirements (standard library only for mbox + ics):
    pip install icalendar  # optional, improves .ics parsing
"""
from __future__ import annotations

import argparse
import email
import email.utils
import mailbox
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
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
    return env


# ---------------------------------------------------------------------------
# Gmail / mbox import
# ---------------------------------------------------------------------------

def _msg_to_text_block(msg):
    """Convert a mailbox.Message to the text block format parse-source.py expects."""
    msg_id = msg.get("Message-ID", "").strip("<>").replace(" ", "_") or str(id(msg))
    parts = [f"Message ID: {msg_id}"]

    for field in ("Subject", "From", "To", "Cc", "Date",
                  "List-Unsubscribe", "List-Id", "Precedence"):
        val = msg.get(field, "")
        if val:
            # Collapse whitespace/newlines in folded headers
            val = re.sub(r"\s+", " ", val).strip()
            parts.append(f"{field}: {val}")

    return "\n".join(parts)


def import_mbox(mbox_path, days=None):
    """Parse a Google Takeout .mbox file into Gmail text format.

    Returns (text, count) where text is blocks separated by \\n---\\n.
    """
    print(f"  Importing Gmail mbox: {mbox_path}", file=sys.stderr)
    mbox = mailbox.mbox(str(mbox_path))

    cutoff = None
    if days:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    blocks = []
    for msg in mbox:
        if cutoff:
            date_str = msg.get("Date", "")
            try:
                parsed = email.utils.parsedate_to_datetime(date_str)
                if parsed.tzinfo is None:
                    from datetime import timezone as tz
                    parsed = parsed.replace(tzinfo=tz.utc)
                if parsed < cutoff:
                    continue
            except Exception:
                pass  # Keep messages with unparseable dates

        blocks.append(_msg_to_text_block(msg))

    print(f"    {len(blocks)} messages", file=sys.stderr)
    return "\n---\n".join(blocks), len(blocks)


# ---------------------------------------------------------------------------
# Calendar / .ics import
# ---------------------------------------------------------------------------

def _parse_ics_basic(ics_text):
    """Basic .ics parser using stdlib. Returns list of event dicts."""
    events = []
    current = None
    for line in ics_text.splitlines():
        # Handle line unfolding (RFC 5545: lines starting with SPACE/TAB are continuations)
        if line.startswith((" ", "\t")) and current is not None:
            key = list(current.keys())[-1]
            current[key] = current[key] + line.strip()
            continue

        if line.strip() == "BEGIN:VEVENT":
            current = {}
        elif line.strip() == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            # Handle property parameters: DTSTART;TZID=America/New_York:20260324T100000
            prop, _, value = line.partition(":")
            prop_name = prop.split(";")[0].upper()
            current[prop_name] = value.strip()

    return events


def _ics_dt_to_iso(dt_str):
    """Convert an iCalendar datetime string to ISO 8601."""
    # Format: 20260324T100000Z or 20260324T100000 or 20260324
    dt_str = dt_str.strip()
    try:
        if len(dt_str) == 8:
            d = datetime.strptime(dt_str, "%Y%m%d")
            return d.strftime("%Y-%m-%d")
        elif dt_str.endswith("Z"):
            d = datetime.strptime(dt_str, "%Y%m%dT%H%M%SZ")
            return d.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            d = datetime.strptime(dt_str[:15], "%Y%m%dT%H%M%S")
            return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return dt_str


def _event_to_text(evt):
    """Format an .ics event dict as text that parse-source.py expects."""
    title = evt.get("SUMMARY", "(no title)")
    start = _ics_dt_to_iso(evt.get("DTSTART", ""))
    event_id = evt.get("UID", "")

    # Parse ATTENDEE lines -- they may be multiple entries (ATTENDEE0, ATTENDEE1... don't exist)
    # All ATTENDEE values were accumulated into a list
    attendees_raw = evt.get("ATTENDEE", "")
    if isinstance(attendees_raw, str):
        attendees_raw = [attendees_raw]

    attendee_parts = []
    for a in attendees_raw:
        # ATTENDEE;CN=Name;...:mailto:email@corp.com
        cn_match = re.search(r"CN=([^;:]+)", a, re.IGNORECASE)
        email_match = re.search(r"mailto:([^\s;]+)", a, re.IGNORECASE)
        if email_match:
            email_addr = email_match.group(1)
            name = cn_match.group(1).strip('"') if cn_match else ""
            if name:
                attendee_parts.append(f"{name} <{email_addr}>")
            else:
                attendee_parts.append(email_addr)

    attendee_str = ", ".join(attendee_parts) if attendee_parts else "None"

    return (
        f'- "{title}"\n'
        f"  Starts: {start}\n"
        f"  Attendees: {attendee_str}\n"
        f"  ID: {event_id}"
    )


def import_ics(ics_path, days=None):
    """Parse a .ics file into Calendar text format. Returns (text, count)."""
    print(f"  Importing Calendar ics: {ics_path}", file=sys.stderr)

    ics_text = Path(ics_path).read_text(encoding="utf-8", errors="replace")

    # Try icalendar library first (handles multi-valued ATTENDEE properly)
    try:
        import icalendar
        cal = icalendar.Calendar.from_ical(ics_text)

        cutoff = None
        if days:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        blocks = []
        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            start_dt = component.get("DTSTART")
            start_val = start_dt.dt if start_dt else None
            if start_val and cutoff:
                if hasattr(start_val, "tzinfo") and start_val.tzinfo:
                    if start_val < cutoff:
                        continue
                elif hasattr(start_val, "year"):
                    # date object
                    if datetime(start_val.year, start_val.month, start_val.day, tzinfo=timezone.utc) < cutoff:
                        continue

            title = str(component.get("SUMMARY", "(no title)"))
            uid = str(component.get("UID", ""))

            if hasattr(start_val, "strftime"):
                if hasattr(start_val, "hour"):
                    start_iso = start_val.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    start_iso = start_val.strftime("%Y-%m-%d")
            else:
                start_iso = str(start_val) if start_val else ""

            attendees = component.get("ATTENDEE")
            if attendees is None:
                attendees = []
            elif not isinstance(attendees, list):
                attendees = [attendees]

            attendee_parts = []
            for a in attendees:
                email_addr = str(a).replace("mailto:", "")
                cn = a.params.get("CN", "") if hasattr(a, "params") else ""
                if cn:
                    attendee_parts.append(f"{cn} <{email_addr}>")
                else:
                    attendee_parts.append(email_addr)

            attendee_str = ", ".join(attendee_parts) if attendee_parts else "None"
            block = (
                f'- "{title}"\n'
                f"  Starts: {start_iso}\n"
                f"  Attendees: {attendee_str}\n"
                f"  ID: {uid}"
            )
            blocks.append(block)

        print(f"    {len(blocks)} events (via icalendar)", file=sys.stderr)
        return "\n".join(blocks), len(blocks)

    except ImportError:
        pass  # Fall through to basic parser

    # Basic parser fallback
    raw_events = _parse_ics_basic(ics_text)
    blocks = []
    for evt in raw_events:
        block = _event_to_text(evt)
        blocks.append(block)

    print(f"    {len(blocks)} events (basic parser)", file=sys.stderr)
    return "\n".join(blocks), len(blocks)


# ---------------------------------------------------------------------------
# Slack export import
# ---------------------------------------------------------------------------

def import_slack_export(export_path, days=None):
    """Parse a Slack workspace export (directory or ZIP) into Slack CSV format.

    Returns (text, count) where text uses the ===CHANNEL=== format.
    """
    export_path = Path(export_path)
    print(f"  Importing Slack export: {export_path}", file=sys.stderr)

    cutoff = None
    if days:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()

    channels_data = {}  # channel_name -> [messages]

    if export_path.suffix.lower() == ".zip":
        import io
        with zipfile.ZipFile(export_path) as zf:
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                parts = name.strip("/").split("/")
                if len(parts) != 2:
                    continue
                channel_name, _ = parts
                if channel_name in ("channels", "users", "integration_logs", "dms"):
                    continue
                data = json_loads_safe(zf.read(name))
                if data:
                    channels_data.setdefault(channel_name, []).extend(data)
    elif export_path.is_dir():
        for json_file in export_path.rglob("*.json"):
            # Skip metadata files
            if json_file.name in ("channels.json", "users.json", "integration_logs.json"):
                continue
            channel_name = json_file.parent.name
            data = json_loads_safe(json_file.read_text())
            if data:
                channels_data.setdefault(channel_name, []).extend(data)
    else:
        print(f"  ERROR: {export_path} is not a directory or .zip file", file=sys.stderr)
        return "", 0

    csv_header = "MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,BotName,FileCount,AttachmentIDs,HasMedia"
    sections = []
    total_msgs = 0

    for channel_name, messages in channels_data.items():
        rows = [csv_header]
        for m in messages:
            if m.get("subtype") or m.get("bot_id"):
                continue
            ts = m.get("ts", "")
            if cutoff and ts and float(ts) < cutoff:
                continue
            user_id = m.get("user", "")
            if not user_id:
                continue
            try:
                dt_str = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ) if ts else ""
            except Exception:
                dt_str = ""
            thread_ts = m.get("thread_ts", "")
            # Determine channel type based on name prefix
            if channel_name.startswith("mpdm-"):
                ch_type = "mpim"
            elif channel_name.startswith("D") and len(channel_name) == 11:
                ch_type = "im"
            else:
                ch_type = "channel"
            row = f"{ts},{user_id},,, {channel_name},{thread_ts},,{dt_str},,,,,,"
            rows.append(row)
            total_msgs += 1

        if len(rows) > 1:
            ch_type = "im" if channel_name.startswith("D") else "channel"
            sections.append(f"===CHANNEL {channel_name} ({ch_type})===\n" + "\n".join(rows))

    print(f"    {total_msgs} messages across {len(sections)} channels", file=sys.stderr)
    return "\n".join(sections), total_msgs


def json_loads_safe(data):
    """Safely parse JSON bytes or string, return None on failure."""
    import json
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        return json.loads(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    env = load_env()

    parser = argparse.ArgumentParser(
        description="Import contacts from exported data files (no-API mode)"
    )
    parser.add_argument(
        "--import-dir",
        default=str(Path(__file__).parent.parent / "data" / "imports"),
        help="Directory containing export files (default: data/imports/)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent.parent / "data" / "tmp"),
        help="Directory for output files (default: data/tmp)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only import messages/events from the last N days (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List detected import files and exit",
    )
    args = parser.parse_args()

    import_dir = Path(args.import_dir)
    output_dir = Path(args.output_dir)

    if not import_dir.exists():
        print(f"Creating import directory: {import_dir}")
        import_dir.mkdir(parents=True, exist_ok=True)

    # Discover import files
    mbox_files = list(import_dir.glob("**/*.mbox"))
    ics_files = list(import_dir.glob("**/*.ics"))
    slack_zips = list(import_dir.glob("**/slack_export*.zip")) + list(import_dir.glob("**/*slack*.zip"))
    slack_dirs = [d for d in import_dir.iterdir() if d.is_dir() and (
        (d / "channels.json").exists() or any(d.rglob("*.json"))
    )] if import_dir.exists() else []

    if args.list or (not mbox_files and not ics_files and not slack_zips and not slack_dirs):
        print(f"\nImport directory: {import_dir}")
        print(f"  Gmail (.mbox):    {mbox_files or 'none found'}")
        print(f"  Calendar (.ics):  {ics_files or 'none found'}")
        print(f"  Slack (zip/dir):  {slack_zips + slack_dirs or 'none found'}")
        print("""
To use import mode:

  Gmail: Go to takeout.google.com, select Gmail, download .mbox file
         Place as data/imports/Gmail.mbox

  Calendar: Go to Google Calendar > Settings > Import & export > Export
            Place .ics file in data/imports/

  Slack: Go to slack.com/admin > Import/Export > Export
         Place exported zip or extracted directory in data/imports/

  Then run: python3 scripts/import-sources.py
""")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    # Gmail
    if mbox_files:
        all_blocks = []
        for f in mbox_files:
            text, count = import_mbox(f, days=args.days)
            if text:
                all_blocks.append(text)
        gmail_text = "\n---\n".join(all_blocks)
        (output_dir / "lc_gmail.txt").write_text(gmail_text)
        results["gmail"] = f"{sum(1 for _ in gmail_text.split('---')) if gmail_text else 0} messages"
        print(f"Gmail: {results['gmail']} -> {output_dir}/lc_gmail.txt")
    else:
        (output_dir / "lc_gmail.txt").write_text("")
        results["gmail"] = "skipped (no .mbox found)"

    # Calendar
    if ics_files:
        all_blocks = []
        for f in ics_files:
            text, count = import_ics(f, days=args.days)
            if text:
                all_blocks.append(text)
        calendar_text = "\n".join(all_blocks)
        (output_dir / "lc_calendar.txt").write_text(calendar_text)
        results["calendar"] = f"{len(all_blocks)} events"
        print(f"Calendar: {results['calendar']} -> {output_dir}/lc_calendar.txt")
    else:
        (output_dir / "lc_calendar.txt").write_text("")
        results["calendar"] = "skipped (no .ics found)"

    # Slack
    slack_sources = slack_zips + slack_dirs
    if slack_sources:
        all_sections = []
        for s in slack_sources:
            text, count = import_slack_export(s, days=args.days)
            if text:
                all_sections.append(text)
        slack_text = "\n".join(all_sections)
        (output_dir / "lc_slack.txt").write_text(slack_text)
        results["slack"] = f"{len(all_sections)} channel(s)"
        print(f"Slack: {results['slack']} -> {output_dir}/lc_slack.txt")
    else:
        (output_dir / "lc_slack.txt").write_text("")
        results["slack"] = "skipped (no slack export found)"

    print("\nImport complete. Next step:")
    print("  ./scripts/process-run.sh <run-id>")
    print("\nOr run the full pipeline:")
    print("  ./scripts/run-collect.sh --provider import")


if __name__ == "__main__":
    main()
