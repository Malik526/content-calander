"""
oauth.py — Instagram API with Instagram Login: the OAuth wire protocol
(Milestone 4.1).

What it does:
  Every request Pickle Batch makes to Meta to connect an Instagram account,
  and nothing about where the result is stored:

    build_authorization_url()        www.instagram.com/oauth/authorize URL
    exchange_code_for_long_lived_token()
        1. POST api.instagram.com/oauth/access_token (authorization code →
           short-lived token, 1 hour, plus user_id and permissions)
        2. GET graph.instagram.com/access_token?grant_type=ig_exchange_token
           (short-lived → long-lived, 60 days)
        The short-lived token never leaves this function.
    refresh_long_lived_token()       GET graph.instagram.com/refresh_access_token
    fetch_account_profile()          GET graph.instagram.com/<version>/me?fields=user_id,username

  Endpoints and parameters were verified against Meta's documentation
  (docs/decisions/0018-instagram-integration-architecture.md Decision 1).
  Instagram Login documents no PKCE, so no code_verifier is sent. Meta's
  docs show some responses wrapped in a one-element "data" list and others
  flat, and user_id as a number or a string; both shapes are accepted and
  normalized here so callers never see the difference.

  The stored (long-lived) credential shape, persisted encrypted by
  publishing/instagram/credential_store.py:
    {access_token, token_type, expires_at, user_id, permissions,
     obtained_at, last_refreshed_at}
  Timestamps are aware-UTC ISO strings; last_refreshed_at is None until the
  first refresh.

Errors:
  Every failure is an InstagramAuthError with a reason_code (same shape as
  TikTokAuthError / PublishError): NETWORK_ERROR, MALFORMED_RESPONSE,
  AUTH_HTTP_ERROR, NOT_CONFIGURED, and InstagramReauthorizationRequiredError
  (REAUTHORIZATION_REQUIRED) when Meta rejects a refresh or a token is past
  its expiry. Messages carry the step and HTTP status only — never a token,
  an authorization code, the app secret or a response body. Transport
  exceptions are re-raised `from None`: requests' own messages include the
  request URL, and the long-lived exchange and refresh carry the app secret
  or the token in their query strings.

Dependencies:
  requests. content_automation.config (app id/secret, hosts, API version),
  publishing.instagram.configuration (SCOPES).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests

from content_automation import config
from content_automation.publishing.instagram.configuration import SCOPES

_TIMEOUT_SECONDS = 30

# Instagram usernames: letters, digits, periods and underscores, at most 30.
# Anything else is not shown rather than displayed as an identity.
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._]{1,30}")


class InstagramAuthError(Exception):
    """An Instagram OAuth request failed or returned something unusable.
    reason_code/http_status mirror TikTokAuthError. provider_error_code is
    Meta's numeric error code when it sent one (e.g. 190, invalid token) —
    an integer only, never Meta's message text."""

    def __init__(
        self, message: str, *, reason_code: str = "AUTH_ERROR", http_status: int | None = None,
        provider_error_code: int | None = None,
    ):
        super().__init__(message)
        self.reason_code = reason_code
        self.http_status = http_status
        self.provider_error_code = provider_error_code


class InstagramReauthorizationRequiredError(InstagramAuthError):
    """The stored credential can't be used or renewed without the user
    connecting Instagram again: no credential, a token past its expiry, or
    Meta rejecting a refresh (HTTP 4xx)."""

    def __init__(self, message: str, *, http_status: int | None = None, provider_error_code: int | None = None):
        super().__init__(
            message, reason_code="REAUTHORIZATION_REQUIRED", http_status=http_status,
            provider_error_code=provider_error_code,
        )


@dataclass(frozen=True)
class InstagramProfile:
    """The connected account's identity from GET /me. user_id is the
    Instagram professional account id (stored, never displayed)."""

    user_id: str | None
    username: str | None


@dataclass(frozen=True)
class _ShortLivedToken:
    access_token: str = field(repr=False)
    user_id: str
    permissions: list[str]


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------

def generate_state() -> str:
    """A fresh, cryptographically random CSRF value for one attempt."""
    return secrets.token_urlsafe(24)


def _require_app_credentials(*, secret: bool) -> None:
    if not config.INSTAGRAM_APP_ID or (secret and not config.INSTAGRAM_APP_SECRET):
        raise InstagramAuthError(
            "Instagram is not configured on this server (INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET).",
            reason_code="NOT_CONFIGURED",
        )


def build_authorization_url(state: str, redirect_uri: str) -> str:
    """The Instagram consent page for one attempt. redirect_uri must be the
    exact registered callback; the same string is stored with the state and
    sent again at code exchange."""
    _require_app_credentials(secret=False)
    params = {
        "client_id": config.INSTAGRAM_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
    }
    return f"{config.INSTAGRAM_AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"


# ---------------------------------------------------------------------------
# HTTP + response normalization
# ---------------------------------------------------------------------------

def _request(step: str, method: str, url: str, *, timeout: float, data: dict | None = None,
             params: dict | None = None) -> dict:
    """One Meta request → its JSON object, or a structured, secret-free
    InstagramAuthError. `step` names the call in messages."""
    try:
        response = requests.request(method, url, data=data, params=params, timeout=timeout)
    except requests.RequestException as exc:
        # Not `from exc`, and no str(exc): the URL in requests' message can
        # carry the app secret or a token.
        raise InstagramAuthError(
            f"Could not reach Instagram ({step}): {type(exc).__name__}.", reason_code="NETWORK_ERROR",
        ) from None

    try:
        payload = response.json()
    except ValueError:
        raise InstagramAuthError(
            f"Instagram returned a non-JSON response ({step}, HTTP {response.status_code}).",
            reason_code="MALFORMED_RESPONSE", http_status=response.status_code,
        ) from None
    if not isinstance(payload, dict):
        raise InstagramAuthError(
            f"Instagram returned an unexpected response ({step}, HTTP {response.status_code}).",
            reason_code="MALFORMED_RESPONSE", http_status=response.status_code,
        )

    # Graph errors are {"error": {...}}; the api.instagram.com token
    # endpoint uses {"error_type", "code", "error_message"}.
    if response.status_code >= 400 or "error" in payload or "error_type" in payload:
        raise InstagramAuthError(
            f"Instagram rejected the request ({step}, HTTP {response.status_code}).",
            reason_code="AUTH_HTTP_ERROR", http_status=response.status_code,
            provider_error_code=_provider_error_code(payload),
        )
    return payload


def _provider_error_code(payload: dict) -> int | None:
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else payload.get("code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _malformed(step: str, problem: str) -> InstagramAuthError:
    # `problem` names a field, never its value.
    return InstagramAuthError(f"Instagram {step} response {problem}.", reason_code="MALFORMED_RESPONSE")


def _unwrap(payload: dict) -> dict:
    """Meta documents some responses as {"data": [{...}]}."""
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return payload


def _required_text(value, step: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _malformed(step, f"is missing {name}")
    return value


def _account_id(value, step: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip().isdigit():
        return value.strip()
    raise _malformed(step, "is missing a valid user_id")


def _permissions(value) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [p.strip() for p in value if isinstance(p, str) and p.strip()]
    return []


def _expires_in(value, step: str) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    raise _malformed(step, "is missing a valid expires_in")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Code exchange → long-lived token
# ---------------------------------------------------------------------------

def _exchange_code_for_short_lived_token(code: str, redirect_uri: str) -> _ShortLivedToken:
    step = "code exchange"
    payload = _unwrap(_request(step, "POST", config.INSTAGRAM_TOKEN_URL, timeout=_TIMEOUT_SECONDS, data={
        "client_id": config.INSTAGRAM_APP_ID,
        "client_secret": config.INSTAGRAM_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": code,
    }))
    return _ShortLivedToken(
        access_token=_required_text(payload.get("access_token"), step, "access_token"),
        user_id=_account_id(payload.get("user_id"), step),
        permissions=_permissions(payload.get("permissions")),
    )


def _exchange_for_long_lived_token(short: _ShortLivedToken, now: datetime) -> dict:
    step = "long-lived token exchange"
    payload = _request(step, "GET", f"{config.INSTAGRAM_GRAPH_BASE}/access_token", timeout=_TIMEOUT_SECONDS, params={
        "grant_type": "ig_exchange_token",
        "client_secret": config.INSTAGRAM_APP_SECRET,
        "access_token": short.access_token,
    })
    return {
        "access_token": _required_text(payload.get("access_token"), step, "access_token"),
        "token_type": payload.get("token_type") if isinstance(payload.get("token_type"), str) else "bearer",
        "expires_at": (now + timedelta(seconds=_expires_in(payload.get("expires_in"), step))).isoformat(),
        "user_id": short.user_id,
        "permissions": short.permissions,
        "obtained_at": now.isoformat(),
        "last_refreshed_at": None,
    }


def exchange_code_for_long_lived_token(code: str, redirect_uri: str) -> dict:
    """Authorization code → the stored long-lived credential (see module
    docstring). redirect_uri must be the exact value the attempt was
    started with. Meta appends "#_" to the code in some redirects; it is
    not part of the code."""
    _require_app_credentials(secret=True)
    if code.endswith("#_"):
        code = code[:-2]
    short = _exchange_code_for_short_lived_token(code, redirect_uri)
    return _exchange_for_long_lived_token(short, _utc_now())


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def refresh_long_lived_token(stored: dict) -> dict:
    """A refreshed copy of a stored long-lived credential: new access_token
    and expires_at, last_refreshed_at = now; account fields unchanged. Needs
    no app secret. Meta only accepts tokens at least 24 hours old and still
    valid — the caller (credential_store) checks that first. A 4xx from
    Meta raises InstagramReauthorizationRequiredError; network and 5xx
    failures raise the plain (retryable) InstagramAuthError."""
    step = "token refresh"
    try:
        payload = _request(step, "GET", f"{config.INSTAGRAM_GRAPH_BASE}/refresh_access_token", timeout=_TIMEOUT_SECONDS, params={
            "grant_type": "ig_refresh_token",
            "access_token": stored["access_token"],
        })
    except InstagramAuthError as exc:
        if exc.reason_code == "AUTH_HTTP_ERROR" and exc.http_status is not None and 400 <= exc.http_status < 500:
            raise InstagramReauthorizationRequiredError(
                f"Instagram rejected the token refresh (HTTP {exc.http_status}). Reconnect Instagram.",
                http_status=exc.http_status, provider_error_code=exc.provider_error_code,
            ) from None
        raise
    now = _utc_now()
    return {
        **stored,
        "access_token": _required_text(payload.get("access_token"), step, "access_token"),
        "token_type": payload.get("token_type") if isinstance(payload.get("token_type"), str) else stored.get("token_type", "bearer"),
        "expires_at": (now + timedelta(seconds=_expires_in(payload.get("expires_in"), step))).isoformat(),
        "last_refreshed_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def fetch_account_profile(access_token: str, *, timeout: float = _TIMEOUT_SECONDS) -> InstagramProfile:
    """GET /me?fields=user_id,username (instagram_business_basic). Missing
    or invalid fields come back as None; the caller decides what to show."""
    step = "profile lookup"
    payload = _unwrap(_request(
        step, "GET", f"{config.INSTAGRAM_GRAPH_BASE}/{config.INSTAGRAM_GRAPH_API_VERSION}/me", timeout=timeout,
        params={"fields": "user_id,username", "access_token": access_token},
    ))
    username = payload.get("username")
    username = username.strip() if isinstance(username, str) else None
    try:
        user_id = _account_id(payload.get("user_id"), step)
    except InstagramAuthError:
        user_id = None
    return InstagramProfile(
        user_id=user_id,
        username=username if username and _USERNAME_PATTERN.fullmatch(username) else None,
    )
