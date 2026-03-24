#!/usr/bin/env python3
"""Collect contacts from Gmail, Calendar, and Slack.

Supports two providers:
  --provider direct  (default) Google OAuth + Slack browser cookies. No Docker, no MCP.
  --provider mcp     Legacy: connects to MCP proxy via SSE.

Usage:
    python3 scripts/collect-sources.py [--days 7] [--email user@example.com] [--provider direct]

Direct provider requires:
    pip install google-api-python-client google-auth-oauthlib pycookiecheat requests
    python3 scripts/setup-auth.py  # one-time auth setup

MCP provider requires:
    pip install mcp
    Docker + local-automation-mcp stack running on localhost:9090
"""
from __future__ import annotations

import argparse
import asyncio
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
    return env


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
    """Replace bare emails in Calendar Attendees lines with 'Name <email>' format."""
    if not name_map:
        return calendar_text
    result = calendar_text
    for email_addr, name in name_map.items():
        result = result.replace(f" {email_addr},", f' {name} <{email_addr}>,')
        result = result.replace(f" {email_addr}\n", f' {name} <{email_addr}>\n')
        result = result.replace(f"Attendees: {email_addr},", f'Attendees: {name} <{email_addr}>,')
        result = result.replace(f"Attendees: {email_addr}\n", f'Attendees: {name} <{email_addr}>\n')
    return result


async def main_async(email, days, output_dir, provider):
    """Collect from all sources, save to files, print summary."""
    # Add scripts/ to path so providers package is importable
    sys.path.insert(0, str(Path(__file__).parent))

    # Lazy-import the right provider module
    if provider == "mcp":
        try:
            import providers.mcp_provider as p
        except ImportError as e:
            print(
                f"MCP provider unavailable: {e}\n"
                "Install with: pip install mcp\n"
                "Or use: --provider direct",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        try:
            import providers.direct_provider as p
        except ImportError as e:
            print(
                f"Direct provider unavailable: {e}\n"
                "Install with: pip install google-api-python-client google-auth-oauthlib pycookiecheat requests\n"
                "Then run: python3 scripts/setup-auth.py",
                file=sys.stderr,
            )
            sys.exit(1)

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
    max_pages = int(os.environ.get("LC_MAX_GMAIL_PAGES", "3"))

    # Gmail
    print(f"Collecting Gmail (last {days}d, max {max_pages} pages) [{provider}]...", file=sys.stderr)
    try:
        gmail_text, page_count = await p.collect_gmail(email, days)
        results["gmail"] = f"{page_count} pages"
    except Exception as e:
        errors["gmail"] = str(e)
        results["gmail"] = f"FAILED: {e}"

    # Calendar
    print(f"Collecting Calendar (last {days}d) [{provider}]...", file=sys.stderr)
    try:
        calendar_text = await p.collect_calendar(email, days)
        results["calendar"] = "ok"
    except Exception as e:
        errors["calendar"] = str(e)
        results["calendar"] = f"FAILED: {e}"

    # Seed Slack cache if needed
    slack_seeded = await p.seed_slack_cache(db_path)
    if slack_seeded:
        print(f"  Slack directory seeded: {slack_seeded} users", file=sys.stderr)

    # Slack
    print(f"Collecting Slack (last {days}d) [{provider}]...", file=sys.stderr)
    slack_text = ""
    try:
        slack_text = await p.collect_slack(days, email)
        results["slack"] = "ok"
    except Exception as e:
        errors["slack"] = str(e)
        results["slack"] = f"FAILED: {e}"

    # Resolve Slack cache misses
    slack_resolved = 0
    if slack_text:
        print("Resolving Slack user cache misses...", file=sys.stderr)
        try:
            user_csv, slack_resolved = await p.resolve_slack_cache_misses(slack_text, db_path)
            if user_csv:
                slack_text = slack_text + "\n---\n" + user_csv
        except Exception as e:
            print(f"  Slack cache resolution failed: {e}", file=sys.stderr)

    # Enrich names from directory
    name_map = {}
    if gmail_text or calendar_text:
        print(f"Enriching names from directory [{provider}]...", file=sys.stderr)
        try:
            name_map = await p.enrich_names_from_directory(gmail_text, calendar_text, db_path)
            if name_map:
                gmail_text = inject_names_into_gmail(gmail_text, name_map)
                calendar_text = inject_names_into_calendar(calendar_text, name_map)
        except Exception as e:
            print(f"  Name enrichment failed: {e}", file=sys.stderr)

    # Backfill incomplete names in DB
    backfilled = 0
    if name_map and Path(db_path).exists():
        print("Backfilling incomplete names in DB...", file=sys.stderr)
        try:
            backfilled = await p.backfill_names(db_path, name_map)
        except Exception as e:
            print(f"  Name backfill failed: {e}", file=sys.stderr)

    # Write output files
    gmail_file.write_text(gmail_text)
    calendar_file.write_text(calendar_text)
    slack_file.write_text(slack_text)

    gmail_size = gmail_file.stat().st_size
    cal_size = calendar_file.stat().st_size
    slack_size = slack_file.stat().st_size

    print(f"Gmail: {results.get('gmail', 'skipped')} -> {gmail_file} ({gmail_size:,}B)")
    print(f"Calendar: {results.get('calendar', 'skipped')} -> {calendar_file} ({cal_size:,}B)")
    print(f"Slack: {results.get('slack', 'skipped')} -> {slack_file} ({slack_size:,}B)")
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
        description="Collect contacts from Gmail, Calendar, and Slack"
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
    parser.add_argument(
        "--provider",
        choices=["direct", "mcp"],
        default=env.get("LC_PROVIDER", "direct"),
        help="Data provider: 'direct' (Google OAuth + Slack cookies) or 'mcp' (legacy Docker stack). Default: direct",
    )
    args = parser.parse_args()

    if not args.email:
        print("Error: --email required or set LC_SELF_EMAIL in .env", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(
        main_async(args.email, args.days, args.output_dir, args.provider)
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
