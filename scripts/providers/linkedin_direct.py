"""LinkedIn direct API provider using the linkedin-api library + Chrome cookie extraction.

Replaces linkedin-scraper-mcp (uvx + patchright + Chromium) with direct Voyager API calls.

Requirements:
    pip install linkedin-api pycookiecheat
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

CREDS_DIR = Path(__file__).parent.parent.parent / "data" / ".credentials"


def _load_li_at_cookie():
    """Load cached li_at cookie, re-extracting from Chrome if expired."""
    creds_file = CREDS_DIR / "linkedin.json"

    if creds_file.exists():
        creds = json.loads(creds_file.read_text())
        age_days = (time.time() - creds.get("extracted_at", 0)) / 86400
        if age_days < 25 and creds.get("li_at"):
            return creds["li_at"]
        print("  LinkedIn li_at cookie expired, re-extracting from Chrome...", file=sys.stderr)

    return _extract_li_at()


def _extract_li_at():
    """Extract the li_at session cookie from Chrome."""
    try:
        from pycookiecheat import chrome_cookies
    except ImportError:
        raise ImportError(
            "pycookiecheat not installed. Run: pip install pycookiecheat"
        )

    print("  Extracting LinkedIn cookie from Chrome...", file=sys.stderr)
    cookies = chrome_cookies("https://www.linkedin.com")
    li_at = cookies.get("li_at", "")
    if not li_at:
        raise RuntimeError(
            "No 'li_at' cookie found for linkedin.com.\n"
            "Make sure you are logged into LinkedIn in Chrome and try again.\n"
            "Fallback: python3 scripts/setup-auth.py linkedin --manual"
        )

    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    creds_data = {"li_at": li_at, "extracted_at": time.time()}
    (CREDS_DIR / "linkedin.json").write_text(json.dumps(creds_data, indent=2))
    print(f"  LinkedIn cookie cached to {CREDS_DIR / 'linkedin.json'}", file=sys.stderr)
    return li_at


class LinkedInDirectClient:
    """Wraps linkedin-api with Chrome cookie-based authentication."""

    def __init__(self):
        try:
            from linkedin_api import Linkedin
        except ImportError:
            raise ImportError(
                "linkedin-api not installed. Run: pip install linkedin-api"
            )
        self._Linkedin = Linkedin
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        li_at = _load_li_at_cookie()

        # linkedin-api normally takes email+password, but its internals just set
        # up a requests.Session with cookies. We patch it post-init.
        # Use a dummy authenticate=False path and inject the cookie manually.
        client = self._Linkedin.__new__(self._Linkedin)
        client.logger = _make_logger()

        import requests
        session = requests.Session()
        session.cookies.set("li_at", li_at, domain=".linkedin.com")
        # JSESSIONID is needed for CSRF; derive it from li_at or use a placeholder
        # The library will refresh it on first call if needed.
        session.headers.update({
            "X-Li-Lang": "en_US",
            "X-RestLi-Protocol-Version": "2.0.0",
            "X-Li-Track": '{"clientVersion":"1.13.1665"}',
            "csrf-token": "ajax:0",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        })
        session.cookies.set("JSESSIONID", '"ajax:0"', domain=".linkedin.com")
        client.client = _LinkedInSessionWrapper(session)
        self._client = client
        return client

    def search_people(self, keywords, company=None, limit=10):
        """Search LinkedIn for people matching keywords. Returns list of profile dicts."""
        client = self._get_client()
        try:
            params = {"keywords": keywords}
            if company:
                params["current_company"] = [company] if isinstance(company, str) else company
            results = client.search_people(**params, limit=limit)
            return results or []
        except Exception as e:
            print(f"  linkedin-api search_people failed: {e}", file=sys.stderr)
            return []

    def get_profile(self, public_id):
        """Get a LinkedIn profile by public_id (slug). Returns profile dict or None."""
        client = self._get_client()
        try:
            return client.get_profile(public_id)
        except Exception as e:
            print(f"  linkedin-api get_profile failed for {public_id}: {e}", file=sys.stderr)
            return None


def _make_logger():
    """Create a no-op logger for linkedin-api."""
    import logging
    logger = logging.getLogger("linkedin_api")
    logger.setLevel(logging.WARNING)
    return logger


class _LinkedInSessionWrapper:
    """Minimal wrapper that linkedin-api's internal client interface expects."""

    def __init__(self, session):
        self._session = session
        self.metadata = {}

    def get(self, url, **kwargs):
        return self._session.get(url, **kwargs)

    def post(self, url, **kwargs):
        return self._session.post(url, **kwargs)


def extract_profile_url(result):
    """Extract a LinkedIn profile URL from a search_people result dict."""
    public_id = result.get("publicIdentifier") or result.get("public_id", "")
    if public_id:
        return f"https://www.linkedin.com/in/{public_id}/"
    # Fallback: try various field names linkedin-api uses across versions
    for key in ("profile_url", "url", "profileUrl"):
        val = result.get(key, "")
        if val and "linkedin.com/in/" in val:
            return val
    return ""


def extract_headline(result):
    """Extract headline/title from a search_people result dict."""
    return (
        result.get("headline", "")
        or result.get("title", "")
        or result.get("occupation", "")
    )


def extract_name(result):
    """Extract display name from a search_people result dict."""
    first = result.get("firstName", "") or result.get("first_name", "")
    last = result.get("lastName", "") or result.get("last_name", "")
    if first or last:
        return f"{first} {last}".strip()
    return result.get("name", "")


def is_first_degree(result):
    """Return True if the search result is a 1st-degree LinkedIn connection."""
    dist = result.get("distance", "") or result.get("memberDistance", "")
    if hasattr(dist, "get"):
        dist = dist.get("value", "")
    return str(dist).upper() in ("DISTANCE_1", "DISTANCE1", "1ST", "1")
