"""
tiktok_auth.py — TikTok OAuth: interactive desktop authorization (PKCE +
localhost callback), manual fallback, token exchange, refresh, and
persistence.

What it does:
  Authenticates against a dedicated TikTok test account via TikTok's OAuth
  2.0 authorization-code flow (Login Kit), completely separate from Google
  Calendar's OAuth (calendar_manager.py) — different provider, different
  client credentials, different cached token file, never shared. See
  docs/decisions/0006-tiktok-publisher-foundation.md.

  TikTok's current Desktop Login Kit documentation supports localhost/
  127.0.0.1 redirect URIs (including a wildcard port) for desktop apps, and
  requires PKCE for the desktop flow — corrected from an earlier
  implementation here that assumed no useful loopback flow existed at all.
  The preferred path is therefore fully interactive, mirroring
  calendar_manager.py's InstalledAppFlow.run_local_server() in spirit
  (start a local server, open/print the URL, catch the redirect) but not
  its mechanism (TikTok has no equivalent library; this hand-rolls the
  PKCE + state + localhost callback per TikTok's documented requirements):

    python3 tiktok_auth.py --authorize

  This generates a fresh cryptographically random `state` and PKCE
  `code_verifier`/`code_challenge` pair for this attempt only (never
  reused across attempts, never persisted to disk), starts a temporary
  HTTP server on an OS-assigned localhost port, opens/prints the
  authorization URL, blocks for the single redirect, verifies the
  returned `state` matches exactly before doing anything else, and
  exchanges the code (with `code_verifier`) for tokens.

  A manual two-command fallback remains for environments where the
  interactive flow can't run (no local port binding, no browser at all) —
  it also generates a fresh PKCE pair and validates state, transiently
  cached at TIKTOK_PENDING_AUTH_PATH only between the two commands and
  deleted immediately on the second one, success or failure:

    python3 tiktok_auth.py --print-auth-url
    python3 tiktok_auth.py --exchange-code <code> --state <state>

  After either flow, get_access_token() transparently refreshes an expired
  access token using the cached refresh token — no browser needed again
  until the refresh token itself expires (TikTok: ~365 days).

Dependencies:
  requests, stdlib http.server/secrets/hashlib. config.py for client
  credentials/scopes/token path.
"""

import argparse
import hashlib
import http.server
import json
import secrets
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from config import (
    TIKTOK_API_BASE,
    TIKTOK_AUTHORIZE_BASE,
    TIKTOK_CLIENT_KEY,
    TIKTOK_CLIENT_SECRET,
    TIKTOK_LOOPBACK_HOST,
    TIKTOK_LOOPBACK_PATH,
    TIKTOK_REDIRECT_URI,
    TIKTOK_SCOPES,
    TIKTOK_TOKEN_PATH,
)

TOKEN_URL = f"{TIKTOK_API_BASE}/v2/oauth/token/"
AUTHORIZE_URL = f"{TIKTOK_AUTHORIZE_BASE}/v2/auth/authorize/"

# Transient cache for the manual two-command fallback only — bridges
# --print-auth-url and --exchange-code (two separate process invocations)
# without ever touching the long-lived credential file early. Deleted
# immediately by --exchange-code, success or failure; never left behind
# longer than that single pending attempt. Outside the repo, alongside the
# real token file.
TIKTOK_PENDING_AUTH_PATH = TIKTOK_TOKEN_PATH.with_name("tiktok_pending_auth.json")

# Refresh this many minutes before the stored expiry, so a token that's
# about to expire mid-request is refreshed proactively rather than reactively.
_REFRESH_SKEW = timedelta(minutes=5)

# How long the interactive flow's local server waits for the browser
# redirect before giving up.
_CALLBACK_TIMEOUT_SECONDS = 180


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


# ---------------------------------------------------------------------------
# PKCE + state
# ---------------------------------------------------------------------------

def generate_state() -> str:
    """A fresh, cryptographically random CSRF-protection value. Generate
    one per authorization attempt — never reuse."""
    return secrets.token_urlsafe(24)


def generate_pkce_pair() -> tuple[str, str]:
    """A fresh (code_verifier, code_challenge) pair for the S256 PKCE
    method — mandatory for TikTok's desktop OAuth flow. Generate a new pair
    for every authorization attempt; never reuse one across attempts, and
    never persist the verifier longer than the single attempt it belongs
    to.

    TikTok's Desktop Login Kit documentation deviates from RFC 7636's
    standard base64url challenge encoding: it requires code_challenge as
    the lowercase hex digest of SHA256(code_verifier), not base64url.
    Confirmed directly from TikTok's docs (developers.tiktok.com/doc/
    login-kit-desktop): "Create the code challenge by hashing the code
    verifier using hex encoding of SHA256." Using base64url here produces
    a challenge TikTok's token endpoint rejects as invalid even though the
    verifier itself is correct."""
    verifier = secrets.token_urlsafe(64)  # ~86 chars, [A-Za-z0-9_-]: within TikTok's required 43-128 and unreserved charset
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()  # TikTok-specific: hex, not base64url
    return verifier, challenge


def _resolve_loopback_target() -> tuple[str, int | None, str]:
    """(host, fixed_port_or_None, path) for the interactive flow's local
    callback server. If TIKTOK_REDIRECT_URI is explicitly configured,
    honor its exact host/port/path (some app registrations require a fixed
    port even though TikTok also supports a wildcard port for desktop
    apps). Otherwise default to an OS-assigned ephemeral port on
    TIKTOK_LOOPBACK_HOST/TIKTOK_LOOPBACK_PATH, relying on that documented
    wildcard-port matching."""
    if TIKTOK_REDIRECT_URI:
        parsed = urlparse(TIKTOK_REDIRECT_URI)
        return parsed.hostname or TIKTOK_LOOPBACK_HOST, parsed.port, parsed.path or "/"
    return TIKTOK_LOOPBACK_HOST, None, TIKTOK_LOOPBACK_PATH


def build_authorization_url(state: str, code_challenge: str, redirect_uri: str) -> str:
    """URL for the user to open in a browser to begin consent. PKCE's
    code_challenge (S256) is always included — TikTok's desktop flow
    requires it."""
    _require_client_credentials()
    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "scope": ",".join(TIKTOK_SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{key}={requests.utils.quote(str(value), safe='')}" for key, value in params.items())
    return f"{AUTHORIZE_URL}?{query}"


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------

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


def exchange_code_for_token(code: str, code_verifier: str, redirect_uri: str) -> dict:
    """Exchange a one-time authorization code for an access/refresh token
    pair, presenting the PKCE code_verifier matching the code_challenge
    sent during authorization. Does not persist it — callers save_token()
    the result."""
    _require_client_credentials()
    payload = _post_token_request({
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    })
    return _token_response_to_stored(payload)


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a new access/refresh token pair. Not a
    PKCE request (refresh_token grant doesn't use code_verifier). Does not
    persist it — callers save the result."""
    _require_client_credentials()
    payload = _post_token_request({
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return _token_response_to_stored(payload)


# ---------------------------------------------------------------------------
# Token / pending-auth persistence
# ---------------------------------------------------------------------------

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


def _save_pending_auth(pending: dict) -> None:
    path = Path(TIKTOK_PENDING_AUTH_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_pending_auth() -> dict | None:
    path = Path(TIKTOK_PENDING_AUTH_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _clear_pending_auth() -> None:
    Path(TIKTOK_PENDING_AUTH_PATH).unlink(missing_ok=True)


def get_access_token() -> str:
    """Return a valid access token, transparently refreshing if the cached
    one is expired (or about to expire within _REFRESH_SKEW). Raises
    TikTokAuthError with an actionable message if authorization has never
    been completed, or if the refresh token itself has expired (requires
    redoing the full authorization flow)."""
    token = load_token()
    if token is None:
        raise TikTokAuthError(
            "No TikTok credentials found. Run the one-time authorization flow first:\n"
            "  python3 tiktok_auth.py --authorize\n"
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
            "  python3 tiktok_auth.py --authorize"
        )

    refreshed = refresh_access_token(token["refresh_token"])
    save_token(refreshed)
    return refreshed["access_token"]


# ---------------------------------------------------------------------------
# Interactive flow: PKCE + state + localhost callback
# ---------------------------------------------------------------------------

class _CallbackResult:
    def __init__(self):
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.error_description: str | None = None


def _make_callback_handler(result: "_CallbackResult") -> type:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib method name)
            params = parse_qs(urlparse(self.path).query)
            result.code = params.get("code", [None])[0]
            result.state = params.get("state", [None])[0]
            result.error = params.get("error", [None])[0]
            result.error_description = params.get("error_description", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body>TikTok authorization received. You can close this window.</body></html>")

        def log_message(self, format, *args):  # noqa: A002 (stdlib signature)
            pass  # silence default request logging to stderr

    return _Handler


def authorize_interactive(
    *, host: str | None = None, port: int | None = None, path: str | None = None,
    open_browser: bool = True, timeout_seconds: int = _CALLBACK_TIMEOUT_SECONDS,
) -> dict:
    """Full interactive desktop OAuth flow per TikTok's current Desktop
    Login Kit documentation: PKCE + state + a temporary localhost callback
    server. Generates a fresh state/verifier for this attempt only (never
    persisted to disk — held in memory for the duration of this call),
    prints (and tries to open) the authorization URL, blocks for the
    single redirect, verifies `state` matches exactly before exchanging
    anything, and caches the resulting token via save_token().

    `host`/`port`/`path` override config for testing; production callers
    should leave them unset (resolved from TIKTOK_REDIRECT_URI if set,
    else an OS-assigned ephemeral port on TIKTOK_LOOPBACK_HOST/PATH).
    """
    _require_client_credentials()
    resolved_host, resolved_port, resolved_path = _resolve_loopback_target()
    host = host if host is not None else resolved_host
    path = path if path is not None else resolved_path
    bind_port = port if port is not None else (resolved_port or 0)

    state = generate_state()
    verifier, challenge = generate_pkce_pair()

    result = _CallbackResult()
    server = http.server.HTTPServer((host, bind_port), _make_callback_handler(result))
    actual_port = server.server_address[1]
    redirect_uri = TIKTOK_REDIRECT_URI or f"http://{host}:{actual_port}{path}"

    auth_url = build_authorization_url(state=state, code_challenge=challenge, redirect_uri=redirect_uri)
    print("Open this URL in a browser, log in as the dedicated TikTok test account, and approve:\n")
    print(auth_url)
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass  # printing the URL above is the guaranteed fallback

    try:
        server.timeout = timeout_seconds
        server.handle_request()  # blocks for exactly one request, or until timeout_seconds elapses
    finally:
        server.server_close()

    if result.error:
        raise TikTokAuthError(
            f"TikTok authorization was denied or failed: {result.error} ({result.error_description})"
        )
    if result.code is None:
        raise TikTokAuthError(
            f"No authorization redirect received within {timeout_seconds}s. "
            "The local callback timed out — try again, or use the manual fallback "
            "(--print-auth-url / --exchange-code)."
        )
    if not secrets.compare_digest(result.state or "", state):
        raise TikTokAuthError(
            "OAuth state mismatch — the callback did not match this authorization attempt. "
            "Refusing to proceed. Start over with --authorize."
        )

    token = exchange_code_for_token(result.code, code_verifier=verifier, redirect_uri=redirect_uri)
    save_token(token)
    return token


# ---------------------------------------------------------------------------
# Manual two-command fallback — also PKCE + state, transiently cached
# between the two invocations
# ---------------------------------------------------------------------------

def start_manual_authorization() -> str:
    """Step 1 of the manual fallback: generate a fresh state/PKCE pair,
    cache them transiently (deleted by complete_manual_authorization,
    success or failure — never kept longer than this one pending attempt),
    and return the authorization URL to print/open."""
    _require_client_credentials()
    redirect_uri = TIKTOK_REDIRECT_URI
    if not redirect_uri:
        raise TikTokAuthError(
            "TIKTOK_REDIRECT_URI must be set in .env to use the manual fallback flow "
            "(it must exactly match a redirect URI registered in the TikTok Developer Portal). "
            "Prefer `python3 tiktok_auth.py --authorize` instead, which needs no fixed redirect URI."
        )
    state = generate_state()
    verifier, challenge = generate_pkce_pair()
    _save_pending_auth({"state": state, "code_verifier": verifier, "redirect_uri": redirect_uri})
    return build_authorization_url(state=state, code_challenge=challenge, redirect_uri=redirect_uri)


def complete_manual_authorization(code: str, state: str) -> dict:
    """Step 2 of the manual fallback: validate `state` against the pending
    attempt from start_manual_authorization, then exchange `code` (with
    the matching code_verifier) for tokens. The pending cache is deleted
    immediately, whether or not validation/exchange succeeds — it is
    single-use."""
    pending = _load_pending_auth()
    _clear_pending_auth()
    if pending is None:
        raise TikTokAuthError("No pending authorization found. Run --print-auth-url first.")
    if not secrets.compare_digest(state or "", pending["state"]):
        raise TikTokAuthError(
            "OAuth state mismatch — the --state value does not match the last --print-auth-url call. "
            "Start over with --print-auth-url."
        )
    token = exchange_code_for_token(code, code_verifier=pending["code_verifier"], redirect_uri=pending["redirect_uri"])
    save_token(token)
    return token


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TikTok OAuth authorization for the dedicated test account.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--authorize", action="store_true",
        help="Run the full interactive flow: PKCE + localhost callback (preferred).",
    )
    group.add_argument(
        "--print-auth-url", action="store_true",
        help="Manual fallback step 1: print the URL and cache a pending PKCE/state pair.",
    )
    group.add_argument(
        "--exchange-code", metavar="CODE",
        help="Manual fallback step 2: exchange the code (requires --state) for tokens.",
    )
    parser.add_argument(
        "--state", metavar="STATE",
        help="The `state` query parameter from the redirect URL — required with --exchange-code.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.authorize:
            authorize_interactive()
            print(f"\nTikTok authorization complete. Token cached at {TIKTOK_TOKEN_PATH}")
        elif args.print_auth_url:
            url = start_manual_authorization()
            print("Open this URL in a browser, log in as the dedicated TikTok test account, and approve:\n")
            print(url)
            print(f"\nAfter approving, note the `code` and `state` query parameters from the redirect to {TIKTOK_REDIRECT_URI}")
            print("and run: python3 tiktok_auth.py --exchange-code <code> --state <state>")
        else:
            if not args.state:
                print("ERROR: --state is required with --exchange-code (copy it from the redirect URL).", file=sys.stderr)
                sys.exit(1)
            complete_manual_authorization(args.exchange_code, args.state)
            print(f"TikTok authorization complete. Token cached at {TIKTOK_TOKEN_PATH}")
    except TikTokAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
