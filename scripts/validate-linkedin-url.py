#!/usr/bin/env python3
"""Validate a LinkedIn profile page against expected contact info.

Reads a fetched LinkedIn profile page from stdin (markdown or HTML) and
checks whether it matches the expected person.

Usage (agent workflow):
    # Agent fetches via WebFetch, pipes to validator:
    echo "$PAGE_CONTENT" | python3 scripts/validate-linkedin-url.py \
        --name "Adam Bellusci" --company-domain "redhat.com" --url "/in/adam-bellusci-0783254/"

    # Batch mode: validate all LinkedIn URLs in the database
    python3 scripts/validate-linkedin-url.py --batch --db-path data/contacts.db

Exit codes:
    0 = valid (name matches)
    1 = invalid (name mismatch or page not found)
    2 = inconclusive (couldn't extract name from page)
"""

import argparse
import re
import sys
from pathlib import Path


def normalize_name(name):
    """Normalize a name for fuzzy comparison."""
    if not name:
        return ""
    name = re.sub(r"[^\w\s]", "", name.lower())
    name = re.sub(r"\s+", " ", name).strip()
    parts = name.split()
    return " ".join(sorted(parts))


def name_matches(expected, found):
    """Check if two names refer to the same person (fuzzy)."""
    if not expected or not found:
        return False

    e = normalize_name(expected)
    f = normalize_name(found)
    if e == f:
        return True

    e_parts = set(expected.lower().split())
    f_parts = set(found.lower().split())
    if len(e_parts & f_parts) >= 1 and (
        e_parts <= f_parts or f_parts <= e_parts
    ):
        return True

    e_first, *_, e_last = expected.lower().split()
    f_first, *_, f_last = found.lower().split()
    if e_last == f_last and (e_first == f_first or e_first[0] == f_first[0]):
        return True

    return False


def extract_name_from_markdown(text):
    """Extract person name from WebFetch markdown output."""
    lines = text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            name = line[2:].strip()
            if name and name.lower() not in (
                "sign up | linkedin",
                "page not found | linkedin",
                "linkedin",
            ):
                return name
    return None


def extract_name_from_html(text):
    """Extract person name from HTML title or OG tags."""
    og = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', text)
    if og:
        name = og.group(1).split(" - ")[0].split(" | ")[0].strip()
        if name.lower() not in ("sign up", "page not found", "linkedin"):
            return name

    title = re.search(r"<title[^>]*>([^<]+)</title>", text)
    if title:
        name = title.group(1).split(" - ")[0].split(" | ")[0].strip()
        if name.lower() not in ("sign up", "page not found", "linkedin"):
            return name

    return None


def extract_headline(text):
    """Extract headline/title from the profile page."""
    lines = text.strip().split("\n")
    name_seen = False
    for line in lines:
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            name_seen = True
            continue
        if name_seen and line and not line.startswith("#"):
            return line
    return None


def check_company(text, company_domain):
    """Check if the company domain appears in the profile."""
    if not company_domain:
        return None
    domain_map = {
        "redhat.com": ["red hat", "redhat"],
        "ibm.com": ["ibm"],
        "il.ibm.com": ["ibm"],
        "google.com": ["google"],
        "microsoft.com": ["microsoft"],
    }
    terms = domain_map.get(company_domain, [company_domain.split(".")[0]])
    text_lower = text.lower()
    return any(term in text_lower for term in terms)


def validate_page(page_content, expected_name, company_domain=None, url=None):
    """Validate a LinkedIn page against expected info.

    Returns (status, found_name, details) where status is:
      'valid'        - name matches
      'mismatch'     - name doesn't match (wrong person)
      'not_found'    - profile doesn't exist
      'inconclusive' - couldn't extract name
    """
    if not page_content or len(page_content.strip()) < 50:
        return "not_found", None, "Empty or minimal page content"

    not_found_signals = [
        "page not found",
        "this page doesn",
        "profile you requested",
        "Error fetching URL",
    ]
    if any(s in page_content.lower() for s in not_found_signals):
        return "not_found", None, "Profile page not found"

    found_name = extract_name_from_markdown(page_content)
    if not found_name:
        found_name = extract_name_from_html(page_content)

    if not found_name:
        return "inconclusive", None, "Could not extract name from page"

    if name_matches(expected_name, found_name):
        company_ok = check_company(page_content, company_domain)
        detail = f"Name matches: '{found_name}'"
        if company_ok is True:
            detail += ", company confirmed"
        elif company_ok is False:
            detail += ", company NOT found in profile (may have left)"
        return "valid", found_name, detail

    return "mismatch", found_name, f"Expected '{expected_name}', found '{found_name}'"


def batch_mode(db_path):
    """Print all LinkedIn URLs that need validation."""
    import sqlite3

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT id, name, linkedin_url, company_domain
           FROM people
           WHERE linkedin_url IS NOT NULL AND status != 'ignored'
           ORDER BY interaction_score DESC"""
    ).fetchall()
    db.close()

    print(f"URLs to validate: {len(rows)}")
    print("---")
    for r in rows:
        slug = r["linkedin_url"].rstrip("/").split("/")[-1]
        print(f"{r['id']}|{r['name']}|{r['linkedin_url']}|{r['company_domain'] or ''}")


def main():
    parser = argparse.ArgumentParser(description="Validate LinkedIn profile pages")
    parser.add_argument("--name", help="Expected person name")
    parser.add_argument("--company-domain", help="Expected company domain")
    parser.add_argument("--url", help="LinkedIn URL (for logging)")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Print all URLs needing validation from the database",
    )
    parser.add_argument("--db-path", default="data/contacts.db")
    args = parser.parse_args()

    if args.batch:
        batch_mode(args.db_path)
        return

    if not args.name:
        print("Error: --name required (or use --batch)", file=sys.stderr)
        sys.exit(2)

    page_content = sys.stdin.read()
    status, found_name, details = validate_page(
        page_content, args.name, args.company_domain, args.url
    )

    print(f"status={status}")
    print(f"found_name={found_name}")
    print(f"details={details}")

    if status == "valid":
        sys.exit(0)
    elif status == "inconclusive":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
