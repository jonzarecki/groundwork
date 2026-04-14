#!/usr/bin/env python3
"""One-time authentication setup for groundwork direct provider.

Sets up credentials for:
  - Google (Gmail, Calendar, Contacts) via OAuth 2.0
  - Slack xoxc/xoxd tokens extracted from Chrome cookies
  - LinkedIn li_at cookie extracted from Chrome

All credentials are saved to data/.credentials/ (gitignored).

Usage:
    python3 scripts/setup-auth.py              # set up everything interactively
    python3 scripts/setup-auth.py google       # Google OAuth only
    python3 scripts/setup-auth.py slack        # Slack token extraction only
    python3 scripts/setup-auth.py linkedin     # LinkedIn cookie only
    python3 scripts/setup-auth.py --check      # check credential status without changes
    python3 scripts/setup-auth.py slack --manual    # manually paste xoxc token
    python3 scripts/setup-auth.py linkedin --manual # manually paste li_at cookie
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path

CREDS_DIR = Path(__file__).parent.parent / "data" / ".credentials"
CLIENT_SECRET_FILE = CREDS_DIR / "client_secret.json"
GOOGLE_TOKEN_FILE = CREDS_DIR / "google.json"
SLACK_TOKEN_FILE = CREDS_DIR / "slack.json"
LINKEDIN_TOKEN_FILE = CREDS_DIR / "linkedin.json"

# ---------------------------------------------------------------------------
# Bundled OAuth client credentials.
# Safe to commit for Desktop app type -- the "secret" is just an app identifier;
# each user still grants consent for their own Google account in their own browser.
# Create at: console.cloud.google.com > Credentials > OAuth 2.0 Client ID > Desktop app
# Then paste the values below and remove the placeholder comments.
# ---------------------------------------------------------------------------
BUNDLED_CLIENT_ID = "342164185329-h5lotfskckqc5b141lqbkuhvrus11pm4.apps.googleusercontent.com"
BUNDLED_CLIENT_SECRET = "BUNDLED_CLIENT_SECRET_REDACTED"

# Google OAuth scopes required by the direct provider
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _ask(prompt, default=""):
    val = input(f"{prompt} [{default}]: ").strip() if default else input(f"{prompt}: ").strip()
    return val or default


def _load_env():
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
# Google OAuth
# ---------------------------------------------------------------------------

def _get_client_config():
    """Return the OAuth client config dict, using the best available source.

    Priority:
      1. BUNDLED_CLIENT_ID/SECRET hardcoded above (zero user friction)
      2. client_secret.json already on disk (user placed it manually)
      3. Interactive: path to a downloaded JSON file, or manual paste
    """
    # 1. Bundled credentials (set by the developer)
    if BUNDLED_CLIENT_ID and BUNDLED_CLIENT_SECRET:
        return {
            "installed": {
                "client_id": BUNDLED_CLIENT_ID,
                "client_secret": BUNDLED_CLIENT_SECRET,
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

    # 2. Previously saved client_secret.json
    if CLIENT_SECRET_FILE.exists():
        return json.loads(CLIENT_SECRET_FILE.read_text())

    # 3. Interactive fallback -- guide user through creating their own GCP client
    print("""
  No Google OAuth client configured.

  You need a Google OAuth client ID to connect Gmail, Calendar, and Contacts.
  This is a one-time setup (5 min):

    1. Go to: https://console.cloud.google.com/
    2. Create or select a project
    3. Enable: Gmail API, Google Calendar API, People API
    4. APIs & Services > Credentials > Create Credentials > OAuth 2.0 Client ID
    5. Application type: Desktop app
    6. Download the JSON file
""")
    path_input = _ask(
        "  Path to downloaded client_secrets JSON (or Enter to paste credentials manually)"
    )
    if path_input and Path(path_input).exists():
        import shutil
        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(path_input, CLIENT_SECRET_FILE)
        print(f"  Copied to {CLIENT_SECRET_FILE}")
        return json.loads(CLIENT_SECRET_FILE.read_text())

    # Manual paste
    print("\n  Manual entry:")
    client_id = _ask("  Client ID (ends with .apps.googleusercontent.com)")
    client_secret = _ask("  Client Secret")
    if not client_id or not client_secret:
        print("  ERROR: client_id and client_secret are required.")
        return None
    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET_FILE.write_text(json.dumps(config))
    return config


def setup_google():
    _print_section("Google OAuth Setup")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except ImportError:
        print("ERROR: Google auth libraries not installed.")
        print("Install with: pip install google-auth-oauthlib google-api-python-client")
        return False

    # Check if already valid
    if GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)
        if creds.valid:
            print(f"  Google credentials are already valid ({GOOGLE_TOKEN_FILE})")
            return True
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
                GOOGLE_TOKEN_FILE.write_text(creds.to_json())
                print("  Google credentials refreshed successfully.")
                return True
            except Exception as e:
                print(f"  Refresh failed ({e}), re-authorizing...")

    # Build client config: prefer bundled credentials, then on-disk file, then manual entry
    client_config = _get_client_config()
    if client_config is None:
        return False

    print("\n  Opening browser for Google authorization...")
    print("  (A browser window will open. Sign in and grant access.)")
    print("  Note: Google will show an 'unverified app' warning -- this is expected.")
    print("  Click 'Advanced' then 'Go to Groundwork (unsafe)' to continue.")
    print("  This happens once. The app only reads Gmail, Calendar, and Contacts.")

    try:
        flow = InstalledAppFlow.from_client_config(client_config, GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
        CREDS_DIR.mkdir(parents=True, exist_ok=True)
        GOOGLE_TOKEN_FILE.write_text(creds.to_json())
        print(f"\n  Google credentials saved to {GOOGLE_TOKEN_FILE}")
        return True
    except Exception as e:
        print(f"  ERROR: OAuth flow failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def setup_slack(manual=False):
    _print_section("Slack Token Setup")

    env = _load_env()
    workspace = env.get("LC_SLACK_WORKSPACE", "")

    if not workspace:
        print("  LC_SLACK_WORKSPACE is not set in .env")
        workspace = _ask("  Enter your Slack workspace subdomain (e.g., 'mycompany' for mycompany.slack.com)")
        if not workspace:
            print("  ERROR: workspace required.")
            return False
        # Write to .env
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            content = env_path.read_text()
            if "LC_SLACK_WORKSPACE" not in content:
                env_path.write_text(content + f"\nLC_SLACK_WORKSPACE={workspace}\n")
                print(f"  Added LC_SLACK_WORKSPACE={workspace} to .env")
        else:
            env_path.write_text(f"LC_SLACK_WORKSPACE={workspace}\n")

    print(f"  Workspace: {workspace}.slack.com")

    if manual:
        return _setup_slack_manual(workspace)

    # Try automatic extraction
    try:
        from pycookiecheat import chrome_cookies
    except ImportError:
        print("  pycookiecheat not installed.")
        print("  Install with: pip install pycookiecheat")
        print("  Or use: python3 scripts/setup-auth.py slack --manual")
        return False

    print("  Extracting cookies from Chrome (Chrome must have an active Slack session)...")
    try:
        cookies = chrome_cookies(f"https://{workspace}.slack.com")
        xoxd = cookies.get("d", "")
        if not xoxd:
            print(f"  ERROR: No 'd' cookie found for {workspace}.slack.com")
            print("  Make sure you are logged into Slack in Chrome.")
            print("  Fallback: python3 scripts/setup-auth.py slack --manual")
            return False

        # Get xoxc from the Slack web app
        print("  Fetching xoxc token from Slack web app...")
        import requests
        resp = requests.get(
            f"https://{workspace}.slack.com",
            cookies={"d": xoxd},
            timeout=15
        )
        xoxc_match = re.search(r'"api_token"\s*:\s*"(xoxc-[^"]+)"', resp.text)
        if not xoxc_match:
            xoxc_match = re.search(r'"token":"(xoxc-[^"]+)"', resp.text)
        if not xoxc_match:
            print("  Could not find xoxc token automatically.")
            print("  Falling back to manual entry...")
            return _setup_slack_manual(workspace, xoxd=xoxd)

        xoxc = xoxc_match.group(1)
        _save_slack_creds(xoxc, xoxd, workspace)
        print(f"  Slack tokens saved to {SLACK_TOKEN_FILE}")
        print(f"    xoxd: {xoxd[:20]}...")
        print(f"    xoxc: {xoxc[:20]}...")
        return True

    except Exception as e:
        print(f"  Cookie extraction failed: {e}")
        print("  Fallback: python3 scripts/setup-auth.py slack --manual")
        return False


def _setup_slack_manual(workspace, xoxd=None):
    """Guide user through manual Slack token extraction."""
    print("""
  Manual Slack token extraction:

  1. Open Chrome and go to: https://app.slack.com/client
  2. Open DevTools (F12 or Cmd+Option+I)
  3. Go to Application > Cookies > https://app.slack.com
  4. Find the cookie named 'd' -- copy its value (starts with xoxd-)
""")
    if not xoxd:
        xoxd = _ask("  Paste the 'd' cookie value (xoxd-...)")
        if not xoxd or not xoxd.startswith("xoxd-"):
            print("  ERROR: invalid xoxd token.")
            return False

    print("""
  5. Now get the xoxc token:
     - In DevTools, go to Console
     - Type: JSON.parse(localStorage.localConfig_v2).teams
     - Find your workspace, look for 'token' field (starts with xoxc-)
""")
    xoxc = _ask("  Paste the xoxc token (xoxc-...)")
    if not xoxc or not xoxc.startswith("xoxc-"):
        print("  ERROR: invalid xoxc token.")
        return False

    _save_slack_creds(xoxc, xoxd, workspace)
    print(f"  Slack tokens saved to {SLACK_TOKEN_FILE}")
    return True


def _save_slack_creds(xoxc, xoxd, workspace):
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    SLACK_TOKEN_FILE.write_text(json.dumps({
        "xoxc": xoxc,
        "xoxd": xoxd,
        "workspace": workspace,
        "extracted_at": time.time(),
    }, indent=2))


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

def setup_linkedin(manual=False):
    _print_section("LinkedIn Cookie Setup")

    if manual:
        return _setup_linkedin_manual()

    try:
        from pycookiecheat import chrome_cookies
    except ImportError:
        print("  pycookiecheat not installed.")
        print("  Install with: pip install pycookiecheat")
        print("  Or use: python3 scripts/setup-auth.py linkedin --manual")
        return False

    print("  Extracting li_at cookie from Chrome (Chrome must be logged into LinkedIn)...")
    try:
        cookies = chrome_cookies("https://www.linkedin.com")
        li_at = cookies.get("li_at", "")
        if not li_at:
            print("  ERROR: No 'li_at' cookie found for linkedin.com")
            print("  Make sure you are logged into LinkedIn in Chrome.")
            print("  Fallback: python3 scripts/setup-auth.py linkedin --manual")
            return False

        _save_linkedin_creds(li_at)
        print(f"  LinkedIn cookie saved to {LINKEDIN_TOKEN_FILE}")
        print(f"    li_at: {li_at[:20]}...")
        return True
    except Exception as e:
        print(f"  Cookie extraction failed: {e}")
        print("  Fallback: python3 scripts/setup-auth.py linkedin --manual")
        return False


def _setup_linkedin_manual():
    """Guide user through manual li_at extraction."""
    print("""
  Manual LinkedIn cookie extraction:

  1. Open Chrome and go to: https://www.linkedin.com
  2. Make sure you are logged in
  3. Open DevTools (F12 or Cmd+Option+I)
  4. Go to Application > Cookies > https://www.linkedin.com
  5. Find the cookie named 'li_at' -- copy its value
""")
    li_at = _ask("  Paste the li_at cookie value")
    if not li_at:
        print("  ERROR: li_at is required.")
        return False

    _save_linkedin_creds(li_at)
    print(f"  LinkedIn cookie saved to {LINKEDIN_TOKEN_FILE}")
    return True


def _save_linkedin_creds(li_at):
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    LINKEDIN_TOKEN_FILE.write_text(json.dumps({
        "li_at": li_at,
        "extracted_at": time.time(),
    }, indent=2))


# ---------------------------------------------------------------------------
# Check status
# ---------------------------------------------------------------------------

def check_status():
    _print_section("Credential Status")

    # Google
    if GOOGLE_TOKEN_FILE.exists():
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE))
            if creds.valid:
                status = "VALID"
            elif creds.expired:
                status = "EXPIRED (will auto-refresh on next use)"
            else:
                status = "INVALID"
        except Exception as e:
            status = f"ERROR: {e}"
    else:
        status = "NOT SET UP  (run: python3 scripts/setup-auth.py google)"
    print(f"  Google:   {status}")

    # Slack
    if SLACK_TOKEN_FILE.exists():
        creds = json.loads(SLACK_TOKEN_FILE.read_text())
        age_days = (time.time() - creds.get("extracted_at", 0)) / 86400
        workspace = creds.get("workspace", "?")
        if age_days < 13:
            status = f"VALID (workspace: {workspace}, extracted {age_days:.0f}d ago)"
        elif age_days < 25:
            status = f"EXPIRING SOON (workspace: {workspace}, {age_days:.0f}d old -- re-run setup)"
        else:
            status = f"LIKELY EXPIRED ({age_days:.0f}d old -- re-run: python3 scripts/setup-auth.py slack)"
    else:
        status = "NOT SET UP  (run: python3 scripts/setup-auth.py slack)"
    print(f"  Slack:    {status}")

    # LinkedIn
    if LINKEDIN_TOKEN_FILE.exists():
        creds = json.loads(LINKEDIN_TOKEN_FILE.read_text())
        age_days = (time.time() - creds.get("extracted_at", 0)) / 86400
        if age_days < 25:
            status = f"VALID (extracted {age_days:.0f}d ago)"
        else:
            status = f"LIKELY EXPIRED ({age_days:.0f}d old -- re-run: python3 scripts/setup-auth.py linkedin)"
    else:
        status = "NOT SET UP  (run: python3 scripts/setup-auth.py linkedin)"
    print(f"  LinkedIn: {status}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Set up authentication for groundwork direct provider"
    )
    parser.add_argument(
        "service",
        nargs="?",
        choices=["google", "slack", "linkedin"],
        help="Which service to set up (default: all)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check credential status without making changes",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Use manual token paste instead of automatic extraction",
    )
    args = parser.parse_args()

    if args.check:
        check_status()
        return

    services = [args.service] if args.service else ["google", "slack", "linkedin"]
    results = {}

    for service in services:
        if service == "google":
            results["google"] = setup_google()
        elif service == "slack":
            results["slack"] = setup_slack(manual=args.manual)
        elif service == "linkedin":
            results["linkedin"] = setup_linkedin(manual=args.manual)

    print("\n" + "="*60)
    print("  Setup complete:")
    for svc, ok in results.items():
        icon = "OK" if ok else "FAILED"
        print(f"    {svc:10s}  {icon}")

    all_ok = all(results.values())
    if all_ok:
        print("\n  All credentials ready. Run: ./scripts/run-collect.sh")
    else:
        failed = [s for s, ok in results.items() if not ok]
        print(f"\n  Fix failed services and re-run: python3 scripts/setup-auth.py {' '.join(failed)}")

    print("="*60)


if __name__ == "__main__":
    main()
