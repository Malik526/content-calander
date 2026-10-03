"""
credential_store.py — hosted, per-connection, encrypted TikTok credential
storage (Milestone 3.6).

What it does:
  publishing/tiktok/auth.py stores exactly one TikTok credential, globally,
  in a single local file (TIKTOK_TOKEN_PATH), guarded by a process-local
  fcntl lock — correct for the local CLI, wrong for a hosted multi-user
  product where each user's TikTok connection needs its own credential.
  This module is the hosted analog: one encrypted credential per
  platform_connections row, persisted via ContentStoreProtocol's
  platform_credentials methods (persistence/content_store.py), refreshed
  under an optimistic-concurrency (CAS) update instead of a file lock.

  Deliberately reuses auth.py's token exchange/refresh/PKCE functions
  verbatim (exchange_code_for_token, refresh_access_token,
  build_authorization_url, generate_state, generate_pkce_pair) via the
  `tiktok_auth` import below — this module never re-implements TikTok's
  OAuth wire protocol, only where the resulting token is stored and how
  staleness/concurrency work. The stored token dict has exactly the same
  shape auth.py's save_token() already writes (access_token,
  refresh_token, access_token_expires_at, refresh_token_expires_at,
  open_id, scope) — see auth._token_response_to_stored. The local-file/
  fcntl path in auth.py is completely untouched and unused here — both
  paths coexist; the CLI keeps using its own token file, hosted
  connections use this module.

Encryption:
  Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`, already a
  transitive dependency of google-auth) with
  config.CREDENTIAL_ENCRYPTION_KEY — a symmetric key generated once and
  set in .env, never derived from anything guessable. Encrypts the token
  dict's JSON serialization; ContentStore never sees plaintext. See
  docs/decisions/0011-real-authentication-and-tiktok-connection.md
  "Credential Storage" for why this (not a KMS/secrets-manager
  integration) is the right-sized mechanism for this milestone.

Concurrency (Milestone 3.6 CAS; Milestone 3.13 per-connection lock):
  get_hosted_tiktok_access_token() returns a still-valid token without any
  locking (the common case). Only when a refresh is needed does it enter
  store.credential_refresh_lock(platform_connection_id) — on Postgres a
  transaction-scoped advisory lock keyed to that one connection
  (PostgresContentStore.credential_refresh_lock), so concurrent worker
  replicas and API requests refreshing the SAME connection queue behind
  each other while other users' connections are never blocked. The
  process holding the lock re-reads the credential first: if a previous
  holder already refreshed it, the fresh token is reused and TikTok's
  refresh endpoint is not called again — exactly one real refresh per
  expiry, closing the Milestone 3.6 residual race (two simultaneous
  refreshes presenting the same, soon-to-be-rotated refresh_token). The
  lock is released on commit, on any exception (a failed refresh rolls the
  transaction back), or if the process dies. Waiting is bounded
  (config.CREDENTIAL_REFRESH_LOCK_TIMEOUT_SECONDS) and surfaces as a
  retryable TikTokAuthError(CREDENTIAL_REFRESH_BUSY), never a hang.

  The CAS write (update_platform_credential_if_unchanged) is kept inside
  the lock as defense in depth, e.g. against a reconnect
  (save_hosted_tiktok_token) landing mid-refresh. On SQLite the lock is a
  no-op and behavior is exactly the Milestone 3.6 CAS loop; the local CLI
  never uses this module at all (auth.py's token file + fcntl lock).

  Logging: connection ids and outcomes only — never token values.

Dependencies:
  cryptography.fernet. content_automation.persistence.protocol.ContentStoreProtocol.
  content_automation.publishing.tiktok.auth (token refresh only).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

from content_automation.config import CREDENTIAL_ENCRYPTION_KEY, TIKTOK_TOKEN_REFRESH_SKEW_SECONDS
from content_automation.persistence.content_store import CredentialRefreshLockTimeout
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.tiktok import auth as tiktok_auth

logger = logging.getLogger(__name__)

_MAX_CAS_ATTEMPTS = 3


class CredentialStoreError(Exception):
    """Misconfiguration (no/invalid encryption key), a stored credential
    that fails to decrypt (wrong/rotated key, corrupted data), or repeated
    CAS contention refreshing a credential."""


def _require_fernet() -> Fernet:
    if not CREDENTIAL_ENCRYPTION_KEY:
        raise CredentialStoreError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with: python3 -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and set it in .env. Never commit it."
        )
    try:
        return Fernet(CREDENTIAL_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CredentialStoreError(f"CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_token(token: dict) -> str:
    return _require_fernet().encrypt(json.dumps(token).encode("utf-8")).decode("ascii")


def decrypt_token(encrypted_payload: str) -> dict:
    try:
        raw = _require_fernet().decrypt(encrypted_payload.encode("ascii"))
    except InvalidToken as exc:
        raise CredentialStoreError(
            "Stored TikTok credential could not be decrypted (wrong/rotated CREDENTIAL_ENCRYPTION_KEY, "
            "or corrupted data)."
        ) from exc
    return json.loads(raw.decode("utf-8"))


def save_hosted_tiktok_token(store: ContentStoreProtocol, platform_connection_id: int, token: dict) -> None:
    """Unconditional overwrite — used by the OAuth connect flow, where a
    fresh, explicit user-initiated authorization always wins (mirrors
    auth.save_token()'s own unconditional overwrite). Never used by the
    refresh path — see get_hosted_tiktok_access_token, which uses the CAS
    update instead so a concurrent refresh can't silently clobber a newer
    one."""
    now = datetime.now(timezone.utc).isoformat()
    store.upsert_platform_credential(platform_connection_id, encrypt_token(token), now)


def load_hosted_tiktok_token(store: ContentStoreProtocol, platform_connection_id: int) -> dict | None:
    record = store.get_platform_credential(platform_connection_id)
    if record is None:
        return None
    return decrypt_token(record.encrypted_payload)


def _token_still_valid(token: dict, now: datetime) -> bool:
    access_expires_at = datetime.fromisoformat(token["access_token_expires_at"])
    return now < access_expires_at - timedelta(seconds=TIKTOK_TOKEN_REFRESH_SKEW_SECONDS)


def get_hosted_tiktok_access_token(store: ContentStoreProtocol, platform_connection_id: int) -> str:
    """Hosted equivalent of auth.get_access_token(): returns a valid access
    token for this connection, transparently refreshing if needed. Raises
    tiktok_auth.TikTokReauthorizationRequiredError if no credential has
    ever been saved for this connection, or its refresh token is itself
    expired/rejected — identical semantics to the local-file path, just
    DB-backed. See the module docstring's "Concurrency" section."""
    record = store.get_platform_credential(platform_connection_id)
    if record is None:
        raise tiktok_auth.TikTokReauthorizationRequiredError(
            "No TikTok credential saved for this connection. Connect TikTok again."
        )
    token = decrypt_token(record.encrypted_payload)
    if _token_still_valid(token, datetime.now(timezone.utc)):
        return token["access_token"]

    try:
        with store.credential_refresh_lock(platform_connection_id):
            return _refresh_under_lock(store, platform_connection_id)
    except CredentialRefreshLockTimeout as exc:
        _log("credential_refresh_lock_timeout", platform_connection_id)
        raise tiktok_auth.TikTokAuthError(
            "Another process is still refreshing this TikTok connection. Try again shortly.",
            reason_code="CREDENTIAL_REFRESH_BUSY",
        ) from exc


def _refresh_under_lock(store: ContentStoreProtocol, platform_connection_id: int) -> str:
    """Re-read, reuse if another holder already refreshed, otherwise refresh
    once and persist. Runs inside credential_refresh_lock."""
    for _ in range(_MAX_CAS_ATTEMPTS):
        record = store.get_platform_credential(platform_connection_id)
        if record is None:
            raise tiktok_auth.TikTokReauthorizationRequiredError(
                "No TikTok credential saved for this connection. Connect TikTok again."
            )
        token = decrypt_token(record.encrypted_payload)
        now = datetime.now(timezone.utc)
        if _token_still_valid(token, now):
            # Refreshed by whoever held the lock before us — reuse it.
            _log("credential_refresh_reused", platform_connection_id)
            return token["access_token"]

        refresh_expires_at = datetime.fromisoformat(token["refresh_token_expires_at"])
        if now >= refresh_expires_at:
            raise tiktok_auth.TikTokReauthorizationRequiredError(
                "This TikTok connection's refresh token has expired. Reconnect TikTok."
            )

        refreshed = tiktok_auth.refresh_access_token(token["refresh_token"])
        new_updated_at = datetime.now(timezone.utc).isoformat()
        won = store.update_platform_credential_if_unchanged(
            platform_connection_id, encrypt_token(refreshed), record.updated_at, new_updated_at,
        )
        if won:
            _log("credential_refreshed", platform_connection_id)
            return refreshed["access_token"]
        # Lost the CAS — something outside the lock (a reconnect) wrote
        # first; re-read rather than trust our superseded refresh result.

    raise CredentialStoreError(
        f"Could not refresh the TikTok credential for connection {platform_connection_id} after "
        f"{_MAX_CAS_ATTEMPTS} attempts — repeated concurrent refresh contention."
    )


def _log(event: str, platform_connection_id: int) -> None:
    """Same key=value shape as scheduling.worker.log_event. Ids only."""
    logger.info(f"event={event} platform_connection_id={platform_connection_id}")
