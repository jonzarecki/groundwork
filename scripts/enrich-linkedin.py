#!/usr/bin/env python3
"""Search LinkedIn for unenriched contacts and save raw results for agent review.

Supports two providers:
  --provider direct  (default) Uses linkedin-api + Chrome li_at cookie. No uvx, no Playwright.
  --provider mcp     Legacy: uses linkedin-scraper-mcp via uvx stdio.

The script handles the deterministic part (query DB, call API, save responses).
The agent handles the judgment part (evaluate candidates, assign confidence, update DB).

Usage:
    python3 scripts/enrich-linkedin.py [--batch-size 10] [--provider direct]

Direct provider requires:
    pip install linkedin-api pycookiecheat
    (Must be logged into LinkedIn in Chrome)

MCP provider requires:
    pip install mcp
    uvx linkedin-scraper-mcp --login --no-headless  (one-time)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
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


def clean_name_for_search(name):
    """Strip parentheticals and other noise from names before LinkedIn search."""
    name = re.sub(r'\s*\([^)]*\)\s*', ' ', name).strip()
    name = re.sub(r'\s+', ' ', name)
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


def get_candidates(db_path, batch_size, exclude_ids=None):
    """Query people who need LinkedIn enrichment."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    exclusion = ""
    params = []
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        exclusion = f"AND id NOT IN ({placeholders})"
        params.extend(exclude_ids)
    params.append(batch_size)
    rows = conn.execute(f"""
        SELECT id, name, company, company_domain, email
        FROM people
        WHERE linkedin_url IS NULL
          AND status NOT IN ('connected', 'ignored')
          AND name LIKE '% %'
          {exclusion}
        ORDER BY interaction_score DESC
        LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# DB logging
# ---------------------------------------------------------------------------

def log_search(db_path, person_id, run_id, search_query, candidates_json, searched_at):
    """Write a linkedin_searches row immediately after a search.
    chosen_url/confidence/notes are left NULL -- filled in by the agent during review."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO linkedin_searches (person_id, run_id, search_query, candidates, searched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (person_id, run_id, search_query, candidates_json, searched_at),
    )
    conn.commit()
    conn.close()


def create_enrich_run(db_path, batch_size, provider):
    """Create a runs record for this enrichment pass, return run_id."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO runs (started_at, source, notes) VALUES (?, 'enrich', ?)",
        (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            f"enrich-linkedin.py --batch-size {batch_size} --provider {provider}",
        ),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return run_id


def finalize_enrich_run(db_path, run_id, searched):
    """Update the runs record when done."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE runs SET finished_at = ?, contacts_found = ?, contacts_updated = 0 WHERE id = ?",
        (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), searched, run_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Direct provider (linkedin-api + pycookiecheat)
# ---------------------------------------------------------------------------

async def search_linkedin_direct(candidates, output_dir, pause_seconds=4, db_path=None, run_id=None):
    """Search LinkedIn using linkedin-api library with Chrome cookie auth."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from providers.linkedin_direct import LinkedInDirectClient, extract_profile_url, extract_headline, extract_name, is_first_degree
    except ImportError as e:
        print(f"LinkedIn direct provider unavailable: {e}", file=sys.stderr)
        print("Install with: pip install linkedin-api pycookiecheat", file=sys.stderr)
        return 0, 0, [str(e)]

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    client = LinkedInDirectClient()
    searched = 0
    saved = 0
    errors = []

    for i, person in enumerate(candidates):
        company = derive_company(person)
        name_clean = clean_name_for_search(person["name"])
        query = f"{name_clean} {company}".strip()

        print(f"  [{i+1}/{len(candidates)}] Searching: {query}", file=sys.stderr)

        try:
            results = await asyncio.to_thread(
                lambda q=query: client.search_people(q, limit=10)
            )

            # Format results like the MCP response for downstream compatibility
            candidates_list = []
            for r in results:
                url = extract_profile_url(r)
                candidates_list.append({
                    "url": url,
                    "name": extract_name(r),
                    "headline": extract_headline(r),
                    "company": r.get("company", ""),
                    "is_1st": is_first_degree(r),
                })

            raw_text = json.dumps(candidates_list, indent=2)

            record = {
                "person_id": person["id"],
                "name": person["name"],
                "email": person.get("email"),
                "company": company,
                "company_domain": person.get("company_domain"),
                "query": query,
                "provider": "direct",
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "raw_response": raw_text,
                "candidates": candidates_list,
            }

            out_file = output / f"{person['id']}.json"
            out_file.write_text(json.dumps(record, indent=2))
            if db_path:
                log_search(
                    db_path, person["id"], run_id,
                    query, json.dumps(candidates_list), record["searched_at"],
                )
            saved += 1

        except Exception as e:
            errors.append(f"{person['name']}: {e}")

        searched += 1
        if i < len(candidates) - 1:
            await asyncio.sleep(pause_seconds)

    return searched, saved, errors


# ---------------------------------------------------------------------------
# MCP provider (linkedin-scraper-mcp via uvx)
# ---------------------------------------------------------------------------

async def search_linkedin_mcp(candidates, output_dir, pause_seconds=4, db_path=None, run_id=None):
    """Search LinkedIn using linkedin-scraper-mcp via uvx stdio."""
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
        return 0, 0, ["mcp not installed"]

    def extract_text(result):
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts)

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    searched = 0
    saved = 0
    errors = []

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
                    name_clean = clean_name_for_search(person["name"])
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
                            "provider": "mcp",
                            "searched_at": datetime.now(timezone.utc).isoformat(),
                            "raw_response": raw_text,
                        }

                        out_file = output / f"{person['id']}.json"
                        out_file.write_text(json.dumps(record, indent=2))
                        if db_path:
                            log_search(
                                db_path, person["id"], run_id,
                                query,
                                json.dumps([{"raw": raw_text}]),
                                record["searched_at"],
                            )
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    parser.add_argument(
        "--provider",
        choices=["direct", "mcp"],
        default=env.get("LC_PROVIDER", "direct"),
        help="'direct' uses linkedin-api + Chrome cookies (no uvx/Playwright), 'mcp' uses linkedin-scraper-mcp",
    )
    args = parser.parse_args()

    db_path = str(Path(__file__).parent.parent / "data" / "contacts.db")
    if not Path(db_path).exists():
        print("Error: database not found. Run ./scripts/run-collect.sh first.", file=sys.stderr)
        sys.exit(1)

    # Skip contacts that already have a pending review file
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    already_pending = {int(f.stem) for f in out_dir.glob("*.json") if f.stem.isdigit()}
    if already_pending:
        print(f"Skipping {len(already_pending)} contacts with pending review files (review them first).")

    candidates = get_candidates(db_path, args.batch_size, exclude_ids=list(already_pending))
    if not candidates:
        print("No contacts need LinkedIn enrichment.")
        print("(All contacts either have linkedin_url, are connected/ignored, or have single-word names)")
        sys.exit(0)

    print(f"LinkedIn enrichment: {len(candidates)} candidates [{args.provider}]", file=sys.stderr)

    run_id = create_enrich_run(db_path, args.batch_size, args.provider)

    if args.provider == "direct":
        searched, saved, errors = asyncio.run(
            search_linkedin_direct(candidates, args.output_dir, args.pause,
                                   db_path=db_path, run_id=run_id)
        )
    else:
        searched, saved, errors = asyncio.run(
            search_linkedin_mcp(candidates, args.output_dir, args.pause,
                                db_path=db_path, run_id=run_id)
        )

    finalize_enrich_run(db_path, run_id, searched)

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
