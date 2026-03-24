#!/usr/bin/env python3
"""Dev server for the viewer with auto-save support."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "contacts.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
MAX_BACKUPS = 20
MIN_DB_SIZE = 4096
SQLITE_HEADER = b"SQLite format 3\x00"


def backup_db(reason: str) -> Path | None:
    if not DB_PATH.exists() or DB_PATH.stat().st_size < MIN_DB_SIZE:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"contacts-{ts}-{reason}.db"
    shutil.copy2(DB_PATH, dest)
    prune_backups()
    return dest


def prune_backups():
    backups = sorted(BACKUP_DIR.glob("contacts-*.db"), key=lambda p: p.stat().st_mtime)
    while len(backups) > MAX_BACKUPS:
        backups.pop(0).unlink()


def validate_db_bytes(data: bytes) -> str | None:
    """Return an error string, or None if valid."""
    if len(data) < MIN_DB_SIZE:
        return f"payload too small ({len(data)} bytes, minimum {MIN_DB_SIZE})"
    if data[:16] != SQLITE_HEADER:
        return "missing SQLite header — not a database file"
    tmp = NamedTemporaryFile(suffix=".db", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "people" not in tables:
            return "database missing 'people' table"
        count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        conn.close()
        if DB_PATH.exists() and DB_PATH.stat().st_size >= MIN_DB_SIZE:
            try:
                old = sqlite3.connect(str(DB_PATH))
                old_count = old.execute("SELECT COUNT(*) FROM people").fetchone()[0]
                old.close()
                if old_count > 0 and count < old_count * 0.5:
                    return (
                        f"row count dropped from {old_count} to {count} "
                        f"(>{50}% loss) — rejecting to prevent data loss"
                    )
            except Exception:
                pass
        return None
    except sqlite3.DatabaseError as e:
        return f"SQLite cannot open the file: {e}"
    finally:
        os.unlink(tmp.name)


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/api/save":
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length)

            error = validate_db_bytes(data)
            if error:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": error}).encode())
                print(f"  [REJECTED] {error}")
                return

            backup_db("pre-save")
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            DB_PATH.write_bytes(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    os.chdir(PROJECT_ROOT)

    bk = backup_db("startup")
    if bk:
        print(f"Startup backup: {bk}")

    server = HTTPServer(("", port), Handler)
    print(f"Serving at http://localhost:{port}")
    print(f"Viewer:  http://localhost:{port}/viewer/index.html")
    print(f"DB path: {DB_PATH}")
    print(f"Backups: {BACKUP_DIR}/ (keeping last {MAX_BACKUPS})")
    server.serve_forever()


if __name__ == "__main__":
    main()
