#!/usr/bin/env python3
"""Search LinkedIn for unenriched contacts and save raw results for agent review.

The script handles the deterministic part (query DB, call MCP, save responses).
The agent handles the judgment part (evaluate candidates, assign confidence, update DB).

Usage:
    python3 scripts/enrich-linkedin.py [--batch-size 10] [--output-dir data/tmp/linkedin]

Requires: pip install mcp (Python 3.10+), linkedin MCP server running
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession, StdioServerParameters
except ImportError:
    print(
        "Error: MCP Python SDK not found.\n"
        "Install with: pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)


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


import re as _re


def clean_name_for_search(name):
    """Strip parentheticals and other noise from names before LinkedIn search."""
    name = _re.sub(r'\s*\([^)]*\)\s*', ' ', name).strip()
    name = _re.sub(r'\s+', ' ', name)
    return name


COMPANY_MAP = {
    "redhat.com": "Red Hat",
    "ibm.com": "IBM",
    "il.ibm.com": "IBM",
    "google.com": "Google",
    "microsoft.com": "Microsoft",
    "amazon.com": "Amazon",
    "meta.com": "Meta",
    "apple.com": "Apple",
}


def extract_text(result):
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def get_candidates(db_path, batch_size):
    """Query people who need LinkedIn enrichment."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, name, company, company_domain, email
        FROM people
        WHERE linkedin_url IS NULL
          AND status NOT IN ('connected', 'ignored')
          AND name LIKE '% %'
        ORDER BY interaction_score DESC
        LIMIT ?
    """, (batch_size,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def derive_company(person):
    """Get a human-readable company name for the search query."""
    if person.get("company"):
        return person["company"]
    domain = person.get("company_domain", "")
    if domain in COMPANY_MAP:
        return COMPANY_MAP[domain]
    if domain:
        return domain.split(".")[0].capitalize()
    return ""


async def search_linkedin(candidates, output_dir, pause_seconds=4):
    """Call search_people for each candidate, save raw responses."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    searched = 0
    saved = 0
    errors = []

    # LinkedIn MCP runs as a local stdio process via uvx
    server_params = StdioServerParameters(
        command="uvx",
        args=["linkedin-scraper-mcp"],
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                for i, person in enumerate(candidates):
                    company = derive_company(person)
                        name_clean = clean_name_for_search(person['name'])
                        query = f"{name_clean} {company}".strip()

                    print(f"  [{i+1}/{len(candidates)}] Searching: {query}", file=sys.stderr)

                    try:
                        result = await session.call_tool(
                            "search_people", {"keywords": query}
                        )
                        raw_text = extract_text(result)

                        record = {
                            "person_id": person["id"],
                            "name": person["name"],
                            "email": person.get("email"),
                            "company": company,
                            "company_domain": person.get("company_domain"),
                            "query": query,
                            "searched_at": datetime.now(timezone.utc).isoformat(),
                            "raw_response": raw_text,
                        }

                        out_file = output / f"{person['id']}.json"
                        out_file.write_text(json.dumps(record, indent=2))
                        saved += 1

                    except Exception as e:
                        errors.append(f"{person['name']}: {e}")

                    searched += 1
                    if i < len(candidates) - 1:
                        await asyncio.sleep(pause_seconds)

    except Exception as e:
        print(f"Error: Could not connect to LinkedIn MCP: {e}", file=sys.stderr)
        print("Make sure you have an active session:", file=sys.stderr)
        print("  uvx linkedin-scraper-mcp --login --no-headless", file=sys.stderr)
        return searched, saved, errors

    return searched, saved, errors


def main():
    env = load_env()

    parser = argparse.ArgumentParser(
        description="Search LinkedIn for unenriched contacts (saves raw results for agent review)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(env.get("LC_ENRICH_BATCH_SIZE", "10")),
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent.parent / "data" / "tmp" / "linkedin"),
    )
    parser.add_argument(
        "--pause",
        type=int,
        default=4,
        help="Seconds between LinkedIn API calls (default: 4)",
    )
    args = parser.parse_args()

    db_path = str(Path(__file__).parent.parent / "data" / "contacts.db")
    if not Path(db_path).exists():
        print("Error: database not found. Run ./scripts/run-collect.sh first.", file=sys.stderr)
        sys.exit(1)

    # Clear stale results from previous runs
    out_dir = Path(args.output_dir)
    if out_dir.exists():
        for old_file in out_dir.glob("*.json"):
            old_file.unlink()

    candidates = get_candidates(db_path, args.batch_size)
    if not candidates:
        print("No contacts need LinkedIn enrichment.")
        print("(All contacts either have linkedin_url, are connected/ignored, or have single-word names)")
        sys.exit(0)

    print(f"LinkedIn enrichment: {len(candidates)} candidates", file=sys.stderr)
    searched, saved, errors = asyncio.run(
        search_linkedin(candidates, args.output_dir, args.pause)
    )

    print(f"Searched: {searched}")
    print(f"Results saved: {saved} -> {args.output_dir}/")
    if errors:
        print(f"Errors: {len(errors)}")
        for e in errors:
            print(f"  {e}")

    if saved > 0:
        print(f"\nAgent: review files in {args.output_dir}/ and evaluate each candidate.")


if __name__ == "__main__":
    main()
