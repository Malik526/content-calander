"""
credential_store.py — hosted, per-connection, encrypted Instagram
credential storage and long-lived-token refresh (Milestone 4.1).

What it does:
  One encrypted long-lived Instagram token per platform_connections row, in
  the shared platform_credentials table (no Instagram-specific table or
  column), through the provider-neutral publishing/credential_encryption.py
  helpers TikTok also uses. The stored shape is publishing/instagram/
  oauth.py's {access_token, token_type, expires_at, user_id, permissions,
  obtained_at, last_refreshed_at}; the short-lived token is never stored.

  get_hosted_instagram_access_token() is the one way any caller (the
  Settings identity lookup today, the Reels publisher in Milestone 4.2) gets
  a usable token for a connection.

Refresh rules (Meta, verified 2026-10-04 — ADR-0018 Decision 1):
  A long-lived token lives 60 days and can be refreshed only while still
  valid and at least 24 hours old; one that lapses can't be refreshed and
  the user must reconnect. This module therefore:
    - raises InstagramReauthorizationRequiredError for a missing or expired
      credential, and when Meta rejects a refresh (HTTP 4xx);
    - refreshes only when the token is at least 24 h old (since it was
      obtained or last refreshed) AND has at most
      config.INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS of validity left;
    - otherwise returns the stored token without calling Meta.
  A transient refresh failure (network, 5xx, malformed response) while the
  stored token is still comfortably valid returns that token — refreshing
  is opportunistic until the token is about to expire — and the next use
  tries again.

Concurrency (same contract as publishing/tiktok/credential_store.py):
  The fast path takes no lock. A refresh runs inside
  store.credential_refresh_lock(platform_connection_id) — a per-connection
  Postgres advisory lock (a no-op on SQLite) — and re-reads the credential
  first: if the previous holder already refreshed it, the token is no
  longer due and is reused without calling Meta. The write is the CAS
  update_platform_credential_if_unchanged, so a reconnect landing
  mid-refresh is never overwritten. A lock wait past
  config.CREDENTIAL_REFRESH_LOCK_TIMEOUT_SECONDS raises a retryable
  InstagramAuthError(CREDENTIAL_REFRESH_BUSY).

  Logging: connection ids and event names only — never token values.

Dependencies:
  publishing.credential_encryption, publishing.instagram.oauth,
  persistence.protocol.ContentStoreProtocol, content_automation.config.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from content_automation import config
from content_automation.persistence.content_store import CredentialRefreshLockTimeout
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.credential_encryption import (
    CredentialStoreError,
    decrypt_credential,
    encrypt_credential,
)
from content_automation.publishing.instagram import oauth as instagram_oauth
from content_automation.publishing.instagram.oauth import InstagramAuthError, InstagramReauthorizationRequiredError

logger = logging.getLogger(__name__)

_MAX_CAS_ATTEMPTS = 3

# Meta refuses to refresh a token younger than this.
MIN_REFRESH_AGE = timedelta(hours=24)
# A token this close to expiry is not handed out after a failed refresh.
_EXPIRY_SKEW = timedelta(minutes=5)


def save_hosted_instagram_token(store: ContentStoreProtocol, platform_connection_id: int, token: dict) -> None:
    """Unconditional overwrite — used only by the OAuth callback, where a
    fresh user authorization always wins. Never used by refresh."""
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_platform_credential(platform_connection_id, encrypt_credential(token), now)


def load_hosted_instagram_token(store: ContentStoreProtocol, platform_connection_id: int) -> dict | None:
    record = store.get_platform_credential(platform_connection_id)
    if record is None:
        return None
    return decrypt_credential(record.encrypted_payload)


def _expires_at(token: dict) -> datetime:
    return datetime.fromisoformat(token["expires_at"])


def _is_expired(token: dict, now: datetime) -> bool:
    return now >= _expires_at(token)


def refresh_due(token: dict, now: datetime) -> bool:
    """True when Meta would accept a refresh (still valid, at least 24 h
    since it was obtained or last refreshed) and the token is inside the
    configured refresh window."""
    if _is_expired(token, now):
        return False
    issued = datetime.fromisoformat(token.get("last_refreshed_at") or token["obtained_at"])
    if now - issued < MIN_REFRESH_AGE:
        return False
    return _expires_at(token) - now <= timedelta(seconds=config.INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS)


def get_hosted_instagram_access_token(store: ContentStoreProtocol, platform_connection_id: int) -> str:
    """A usable long-lived access token for this connection, refreshing it
    first when due (see module docstring). Raises
    InstagramReauthorizationRequiredError, InstagramAuthError
    (CREDENTIAL_REFRESH_BUSY / transient refresh failure near expiry) or
    CredentialStoreError (undecryptable credential, CAS contention)."""
    record = store.get_platform_credential(platform_connection_id)
    if record is None:
        raise InstagramReauthorizationRequiredError("No Instagram credential saved for this connection. Connect Instagram again.")
    token = decrypt_credential(record.encrypted_payload)
    now = datetime.now(timezone.utc)
    if _is_expired(token, now):
        raise InstagramReauthorizationRequiredError("This Instagram connection's token has expired. Reconnect Instagram.")
    if not refresh_due(token, now):
        return token["access_token"]

    try:
        with store.credential_refresh_lock(platform_connection_id):
            return _refresh_under_lock(store, platform_connection_id)
    except CredentialRefreshLockTimeout:
        _log("credential_refresh_lock_timeout", platform_connection_id)
        raise InstagramAuthError(
            "Another process is still refreshing this Instagram connection. Try again shortly.",
            reason_code="CREDENTIAL_REFRESH_BUSY",
        ) from None


def _refresh_under_lock(store: ContentStoreProtocol, platform_connection_id: int) -> str:
    """Re-read, reuse if another holder already refreshed, otherwise refresh
    once and persist with CAS. Runs inside credential_refresh_lock."""
    for _ in range(_MAX_CAS_ATTEMPTS):
        record = store.get_platform_credential(platform_connection_id)
        if record is None:
            raise InstagramReauthorizationRequiredError("No Instagram credential saved for this connection. Connect Instagram again.")
        token = decrypt_credential(record.encrypted_payload)
        now = datetime.now(timezone.utc)
        if _is_expired(token, now):
            raise InstagramReauthorizationRequiredError("This Instagram connection's token has expired. Reconnect Instagram.")
        if not refresh_due(token, now):
            # Refreshed by whoever held the lock before us (or reconnected).
            _log("credential_refresh_reused", platform_connection_id)
            return token["access_token"]

        try:
            refreshed = instagram_oauth.refresh_long_lived_token(token)
        except InstagramReauthorizationRequiredError:
            _log("credential_refresh_rejected", platform_connection_id)
            raise
        except InstagramAuthError as exc:
            if now < _expires_at(token) - _EXPIRY_SKEW:
                _log("credential_refresh_deferred", platform_connection_id, reason_code=exc.reason_code)
                return token["access_token"]
            raise

        won = store.update_platform_credential_if_unchanged(
            platform_connection_id, encrypt_credential(refreshed), record.updated_at,
            datetime.now(timezone.utc).isoformat(),
        )
        if won:
            _log("credential_refreshed", platform_connection_id)
            return refreshed["access_token"]
        # Lost the CAS — a reconnect wrote first; re-read rather than trust
        # our superseded refresh result.

    raise CredentialStoreError(
        f"Could not refresh the Instagram credential for connection {platform_connection_id} after "
        f"{_MAX_CAS_ATTEMPTS} attempts — repeated concurrent refresh contention."
    )


def _log(event: str, platform_connection_id: int, **fields) -> None:
    """Same key=value shape as scheduling.worker.log_event. Ids and codes only."""
    extra = "".join(f" {key}={value}" for key, value in fields.items())
    logger.info(f"event=instagram_{event} platform_connection_id={platform_connection_id}{extra}")
