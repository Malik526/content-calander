"""
tiktok_auth.py — TikTok OAuth: one-time authorization, token exchange,
refresh, and persistence.

What it does:
  Authenticates against a dedicated TikTok test account via TikTok's OAuth
  2.0 authorization-code flow (Login Kit), completely separate from Google
  Calendar's OAuth (calendar_manager.py) — different provider, different
  client credentials, different cached token file, never shared. See
  docs/decisions/0006-tiktok-publisher-foundation.md.

  TikTok's OAuth does not offer an InstalledAppFlow-style loopback flow
  (calendar_manager.py's local-server approach doesn't have a TikTok
  equivalent this codebase can rely on) — the redirect_uri must exactly
  match one registered in the TikTok Developer Portal for this app, which
  this project cannot stand a listener on automatically. Authorization is
  therefore a deliberately manual two-step CLI flow (see `python3
  tiktok_auth.py --help`):

    1. `python3 tiktok_auth.py --print-auth-url` prints the URL to open in
       a browser, log into the dedicated TikTok test account, and approve.
    2. TikTok redirects to TIKTOK_REDIRECT_URI with `?code=...` in the
       query string. Copy that `code` value and run
       `python3 tiktok_auth.py --exchange-code <code>` to complete
       authorization and cache the resulting access/refresh token pair.

  After that, get_access_token() transparently refreshes an expired access
  token using the cached refresh token — no browser needed again until the
  refresh token itself expires (TikTok: ~365 days).

Dependencies:
  requests. config.py for client credentials/scopes/token path.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from config import (
    TIKTOK_API_BASE,
    TIKTOK_AUTHORIZE_BASE,
    TIKTOK_CLIENT_KEY,
    TIKTOK_CLIENT_SECRET,
    TIKTOK_REDIRECT_URI,
    TIKTOK_SCOPES,
    TIKTOK_TOKEN_PATH,
)

TOKEN_URL = f"{TIKTOK_API_BASE}/v2/oauth/token/"
AUTHORIZE_URL = f"{TIKTOK_AUTHORIZE_BASE}/v2/auth/authorize/"

# Refresh this many minutes before the stored expiry, so a token that's
# about to expire mid-request is refreshed proactively rather than reactively.
_REFRESH_SKEW = timedelta(minutes=5)


class TikTokAuthError(Exception):
    """OAuth credentials are missing, invalid, or could not be
    obtained/refreshed. Always carries an actionable message."""


def _require_client_credentials() -> None:
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        raise TikTokAuthError(
            "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are not set.\n"
            "One-time setup: create an app in the TikTok Developer Portal "
            "(https://developers.tiktok.com/) with Login Kit and the Content "
            "Posting API enabled, then set both in .env. Never commit these "
            "values. See README.md 'TikTok Publishing Setup'."
        )


def build_authorization_url(state: str) -> str:
    """URL for the user to open in a browser to begin the one-time consent
    flow (step 1 of the manual flow described in the module docstring)."""
    _require_client_credentials()
    if not TIKTOK_REDIRECT_URI:
        raise TikTokAuthError(
            "TIKTOK_REDIRECT_URI is not set. It must exactly match a redirect URI "
            "registered for this app in the TikTok Developer Portal. Set it in .env."
        )
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "scope": ",".join(TIKTOK_SCOPES),
        "response_type": "code",
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "state": state,
    }
    query = "&".join(f"{key}={requests.utils.quote(str(value), safe='')}" for key, value in params.items())
    return f"{AUTHORIZE_URL}?{query}"


def _token_response_to_stored(payload: dict) -> dict:
    """Normalize a TikTok token-endpoint response into the shape persisted
    at TIKTOK_TOKEN_PATH: absolute expiry timestamps computed once here, so
    every later read doesn't need to redo "issued_at + expires_in" math."""
    now = datetime.now(timezone.utc)
    try:
        access_token = payload["access_token"]
        refresh_token = payload["refresh_token"]
        expires_in = payload["expires_in"]
        refresh_expires_in = payload["refresh_expires_in"]
    except KeyError as exc:
        raise TikTokAuthError(f"TikTok token response missing expected field {exc}: {payload!r}") from exc

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_token_expires_at": (now + timedelta(seconds=refresh_expires_in)).isoformat(),
        "open_id": payload.get("open_id"),
        "scope": payload.get("scope"),
    }


def _post_token_request(data: dict) -> dict:
    try:
        response = requests.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TikTokAuthError(f"Could not reach TikTok's token endpoint: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise TikTokAuthError(
            f"TikTok token endpoint returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:200]!r}"
        ) from exc

    if response.status_code >= 400 or "error" in payload:
        raise TikTokAuthError(f"TikTok token endpoint error (HTTP {response.status_code}): {payload!r}")

    return payload


def exchange_code_for_token(code: str) -> dict:
    """Exchange a one-time authorization code (from the browser redirect,
    step 2 of the manual flow) for an access/refresh token pair. Does not
    persist it — call save_token() with the result, or use the
    `--exchange-code` CLI flag which does both."""
    _require_client_credentials()
    payload = _post_token_request({
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TIKTOK_REDIRECT_URI,
    })
    return _token_response_to_stored(payload)


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access/refresh token pair. Does
    not persist it — callers save the result."""
    _require_client_credentials()
    payload = _post_token_request({
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return _token_response_to_stored(payload)


def load_token() -> dict | None:
    path = Path(TIKTOK_TOKEN_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_token(token: dict) -> None:
    path = Path(TIKTOK_TOKEN_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX permissions


def get_access_token() -> str:
    """Return a valid access token, transparently refreshing if the cached
    one is expired (or about to expire within _REFRESH_SKEW). Raises
    TikTokAuthError with an actionable message if authorization has never
    been completed, or if the refresh token itself has expired (requires
    redoing the full manual authorization flow)."""
    token = load_token()
    if token is None:
        raise TikTokAuthError(
            "No TikTok credentials found. Run the one-time authorization flow first:\n"
            "  python3 tiktok_auth.py --print-auth-url\n"
            "  (open it in a browser, log in as the dedicated TikTok test account, approve)\n"
            "  python3 tiktok_auth.py --exchange-code <code from the redirect URL>\n"
            "See README.md 'TikTok Publishing Setup'."
        )

    now = datetime.now(timezone.utc)
    access_expires_at = datetime.fromisoformat(token["access_token_expires_at"])
    if now < access_expires_at - _REFRESH_SKEW:
        return token["access_token"]

    refresh_expires_at = datetime.fromisoformat(token["refresh_token_expires_at"])
    if now >= refresh_expires_at:
        raise TikTokAuthError(
            "TikTok refresh token has expired. Re-run the full authorization flow:\n"
            "  python3 tiktok_auth.py --print-auth-url"
        )

    refreshed = refresh_access_token(token["refresh_token"])
    save_token(refreshed)
    return refreshed["access_token"]


# ---------------------------------------------------------------------------
# CLI — the manual one-time authorization flow
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time TikTok OAuth authorization for the dedicated test account."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--print-auth-url", action="store_true",
        help="Print the URL to open in a browser to begin authorization.",
    )
    group.add_argument(
        "--exchange-code", metavar="CODE",
        help="Exchange the authorization code from the redirect URL for a cached access/refresh token.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.print_auth_url:
            url = build_authorization_url(state="content-calendar")
            print("Open this URL in a browser, log in as the dedicated TikTok test account, and approve:\n")
            print(url)
            print(f"\nAfter approving, copy the `code` query parameter from the redirect to {TIKTOK_REDIRECT_URI}")
            print("and run: python3 tiktok_auth.py --exchange-code <code>")
        else:
            token = exchange_code_for_token(args.exchange_code)
            save_token(token)
            print(f"TikTok authorization complete. Token cached at {TIKTOK_TOKEN_PATH}")
    except TikTokAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
