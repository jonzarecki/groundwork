#!/usr/bin/env python3
"""Batch-save LinkedIn enrichment results to the database.

The agent writes a TSV or calls this script directly with --rows JSON.
One DB open, one transaction — no repeated sqlite3 invocations.

Usage (TSV file):
    python3 scripts/save-linkedin-batch.py results.tsv

TSV format (tab-separated, no header required if using positional columns):
    person_id  url  confidence  query  notes  [candidates_json]

  url = "null" or empty to log a failed search.
  candidates_json is optional (defaults to []).

Usage (inline JSON — best for agent use):
    python3 scripts/save-linkedin-batch.py --rows '[
        {"id": 1078, "url": "https://www.linkedin.com/in/michael-kotelnikov/",
         "confidence": "high", "query": "Michael Kotelnikov Red Hat",
         "notes": "Name + Red Hat. 20+ mutuals.", "candidates": [...]},
        {"id": 973, "url": null, "confidence": null,
         "query": "Kavitha Srinivasan Red Hat", "notes": "No Red Hat match found."}
    ]'

Options:
    --run-id N    Override run ID (default: MAX(id) from runs table)
    --db PATH     Database path (default: data/contacts.db)
    --dry-run     Print SQL without executing
"""
from __future__ import annotations

import argparse
import json
import sqlite3
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


def parse_tsv(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        row = {
            "id": int(parts[0].strip()),
            "url": parts[1].strip() or None,
            "confidence": parts[2].strip() if len(parts) > 2 else None,
            "query": parts[3].strip() if len(parts) > 3 else "",
            "notes": parts[4].strip() if len(parts) > 4 else "",
            "candidates": json.loads(parts[5]) if len(parts) > 5 and parts[5].strip() else [],
        }
        if row["url"] in ("null", ""):
            row["url"] = None
        if row["confidence"] in ("null", ""):
            row["confidence"] = None
        rows.append(row)
    return rows


def save_batch(rows: list[dict], db_path: str, run_id: int, dry_run: bool = False):
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    saved = 0
    skipped = 0

    for row in rows:
        person_id = int(row["id"])
        url = row.get("url") or None
        confidence = row.get("confidence") or None
        query = row.get("query", "")
        notes = row.get("notes", "")
        candidates = json.dumps(row.get("candidates", []))

        # Derive query from DB if not provided
        if not query:
            cur.execute("SELECT name, company FROM people WHERE id = ?", (person_id,))
            r = cur.fetchone()
            if r:
                query = f"{r[0]} {r[1] or ''}".strip()

        if dry_run:
            print(f"-- [{person_id}] url={url!r} confidence={confidence!r}")
            print(f"   UPDATE people SET linkedin_url=..., linkedin_confidence=... WHERE id={person_id};")
            print(f"   INSERT INTO linkedin_searches (...) VALUES ({person_id}, {run_id}, ...);")
            continue

        cur.execute("""
            UPDATE people SET
              linkedin_url = ?,
              linkedin_confidence = ?,
              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE id = ?
        """, (url, confidence, person_id))

        cur.execute("""
            INSERT INTO linkedin_searches
              (person_id, run_id, search_query, candidates, chosen_url, confidence, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (person_id, run_id, query, candidates, url, confidence, notes))

        cur.execute("SELECT name FROM people WHERE id = ?", (person_id,))
        name = (cur.fetchone() or ("?",))[0]
        status = f"-> {url} ({confidence})" if url else "-> no match"
        print(f"  [{person_id}] {name} {status}")
        saved += 1

    if not dry_run:
        con.commit()
        print(f"\nDone: {saved} saved, {skipped} skipped.")
    con.close()


def main():
    env = load_env()
    default_db = str(Path(__file__).parent.parent / "data" / "contacts.db")

    parser = argparse.ArgumentParser(description="Batch-save LinkedIn enrichment results")
    parser.add_argument("tsv", nargs="?", help="TSV file of results")
    parser.add_argument("--rows", help="JSON array of result objects (inline)")
    parser.add_argument("--run-id", type=int, help="Run ID (default: latest)")
    parser.add_argument("--db", default=env.get("LC_DB_PATH", default_db))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.tsv and not args.rows:
        parser.print_help()
        sys.exit(1)

    # Load rows
    if args.rows:
        rows = json.loads(args.rows)
    else:
        rows = parse_tsv(args.tsv)

    if not rows:
        print("No rows to process.")
        sys.exit(0)

    # Resolve run_id
    run_id = args.run_id
    if not run_id:
        con = sqlite3.connect(args.db)
        run_id = con.execute("SELECT MAX(id) FROM runs").fetchone()[0] or 0
        con.close()

    print(f"Saving {len(rows)} results (run_id={run_id}, db={args.db})")
    save_batch(rows, args.db, run_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
