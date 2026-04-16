#!/usr/bin/env python3
"""notify-run.py -- Generate a contact suggestions digest after a Groundwork collect run.

Usage:
  python3 scripts/notify-run.py [--run-id N] [--db PATH] [--min-score N] [--format json|message]

Outputs a structured digest of notable new contacts and enrichment candidates
from the specified (or latest) completed collect run.  Exits with code 0 and
prints nothing if there is nothing actionable (no flags, no notable contacts,
no enrichment candidates) unless --format json is requested.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).parent.parent / "data" / "contacts.db"
DEFAULT_MIN_SCORE = 15


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_latest_run(conn: sqlite3.Connection, run_id: int | None) -> dict | None:
    if run_id is not None:
        row = conn.execute(
            "SELECT * FROM runs WHERE id = ? AND finished_at IS NOT NULL", (run_id,)
        ).fetchone()
    else:
        # Latest completed collect run (source = 'all')
        row = conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL AND source = 'all'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_new_contacts_count(conn: sqlite3.Connection, run_id: int) -> int:
    """Count all people whose first (and only) sightings are in this run."""
    return conn.execute(
        """
        SELECT COUNT(DISTINCT p.id)
        FROM people p
        JOIN sightings s ON s.person_id = p.id
        WHERE s.run_id = ?
          AND p.status != 'ignored'
          AND p.id NOT IN (
              SELECT DISTINCT person_id FROM sightings
              WHERE run_id != ? AND person_id IS NOT NULL
          )
        """,
        (run_id, run_id),
    ).fetchone()[0]


def get_notable_contacts(
    conn: sqlite3.Connection, run_id: int, min_score: int
) -> list[dict]:
    """New contacts this run (first appearance) with interaction_score >= min_score."""
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.email, p.company, p.interaction_score,
               p.channel_diversity, p.sources, p.linkedin_url
        FROM people p
        JOIN sightings s ON s.person_id = p.id
        WHERE s.run_id = ?
          AND p.status != 'ignored'
          AND p.interaction_score >= ?
          AND p.id NOT IN (
              SELECT DISTINCT person_id FROM sightings
              WHERE run_id != ? AND person_id IS NOT NULL
          )
        GROUP BY p.id
        ORDER BY p.interaction_score DESC
        LIMIT 10
        """,
        (run_id, min_score, run_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_enrichment_candidates(conn: sqlite3.Connection, min_score: int) -> list[dict]:
    """People without a LinkedIn URL who score high enough to be worth enriching."""
    rows = conn.execute(
        """
        SELECT id, name, email, company, interaction_score, channel_diversity
        FROM people
        WHERE linkedin_url IS NULL
          AND interaction_score >= ?
          AND status NOT IN ('ignored', 'wrong_match')
        ORDER BY interaction_score DESC
        LIMIT 5
        """,
        (min_score,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_flags(conn: sqlite3.Connection) -> dict:
    unresolved = conn.execute(
        "SELECT COUNT(*) FROM sightings WHERE person_id IS NULL"
    ).fetchone()[0]

    dupes = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT a.id FROM people a
            JOIN people b ON a.id < b.id
            WHERE LOWER(a.name) = LOWER(b.name)
              AND a.company_domain = b.company_domain
              AND a.company_domain IS NOT NULL
              AND a.status != 'ignored'
              AND b.status != 'ignored'
        )
        """
    ).fetchone()[0]

    incomplete = conn.execute(
        """
        SELECT COUNT(*) FROM people
        WHERE name NOT LIKE '% %'
          AND interaction_score >= 5
          AND status != 'ignored'
        """
    ).fetchone()[0]

    return {
        "unresolved_sightings": unresolved,
        "duplicate_pairs": dupes,
        "incomplete_names": incomplete,
        "total": unresolved + dupes + incomplete,
    }


def format_message(data: dict) -> str:
    """Compact human-readable digest for chat platforms (WhatsApp, Slack, Discord)."""
    run = data["run"]
    lines: list[str] = [f"Groundwork run #{run['run_id']} — {run['run_date']}"]

    new_count = data["new_contacts"]
    if new_count:
        lines.append(f"{new_count} new contact{'s' if new_count != 1 else ''} collected.")
    else:
        lines.append("No new contacts this run.")

    notable = data["notable_contacts"]
    if notable:
        lines.append("")
        lines.append("Notable new contacts:")
        for p in notable:
            channels = p["sources"] or ""
            lines.append(
                f"  \u2022 {p['name']} (score {p['interaction_score']}, {channels})"
            )

    candidates = data["enrichment_candidates"]
    if candidates:
        names = ", ".join(p["name"] for p in candidates[:3])
        rest = len(candidates) - 3
        suffix = f" +{rest} more" if rest > 0 else ""
        lines.append(
            f"\n{len(candidates)} ready for LinkedIn enrichment: {names}{suffix}"
        )

    flags = data["flags"]
    if flags["total"]:
        lines.append(f"\nFlagged for review: {flags['total']}")
        if flags["duplicate_pairs"]:
            lines.append(f"  \u2022 {flags['duplicate_pairs']} duplicate pair(s)")
        if flags["unresolved_sightings"]:
            lines.append(f"  \u2022 {flags['unresolved_sightings']} unresolved sighting(s)")
        if flags["incomplete_names"]:
            lines.append(f"  \u2022 {flags['incomplete_names']} incomplete name(s)")

    actions: list[str] = []
    if candidates:
        actions.append('"enrich"')
    if flags["total"]:
        actions.append('"review"')
    if notable:
        first_name = notable[0]["name"].split()[0]
        actions.append(f'"show {first_name}"')

    if actions:
        lines.append(f"\nReply {', '.join(actions)} to continue.")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Groundwork post-run contact digest",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--run-id", type=int, default=None,
        help="Run ID to report on (defaults to latest completed collect run)",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB),
        help="Path to contacts.db",
    )
    parser.add_argument(
        "--min-score", type=int, default=DEFAULT_MIN_SCORE,
        help="Minimum interaction_score for a contact to appear in the digest",
    )
    parser.add_argument(
        "--format", choices=["json", "message"], default="message",
        help="Output format",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = open_db(str(db_path))

    run = get_latest_run(conn, args.run_id)
    if run is None:
        print("Error: no completed collect runs found in database.", file=sys.stderr)
        sys.exit(1)

    run_id = run["id"]
    new_contacts = get_new_contacts_count(conn, run_id)
    notable = get_notable_contacts(conn, run_id, args.min_score)
    enrichment = get_enrichment_candidates(conn, args.min_score)
    flags = get_flags(conn)

    data: dict = {
        "run": {
            "run_id": run_id,
            "run_date": (run["finished_at"] or run["started_at"])[:10],
            "sightings": run["contacts_found"],
        },
        "new_contacts": new_contacts,
        "notable_contacts": notable,
        "enrichment_candidates": enrichment,
        "flags": flags,
    }

    if args.format == "json":
        print(json.dumps(data, indent=2))
    else:
        # In message mode, only print if there is something actionable
        has_content = (
            new_contacts > 0
            or notable
            or enrichment
            or flags["total"] > 0
        )
        if has_content:
            print(format_message(data))


if __name__ == "__main__":
    main()
