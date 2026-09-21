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

Concurrency — a known, documented residual limitation (Phase 15's own
allowance: "if a database-backed CAS/lock is simple and justified now,
implement it... otherwise document the remaining hosted-worker lock issue
explicitly"):
  get_hosted_tiktok_access_token()'s refresh path uses
  update_platform_credential_if_unchanged (optimistic concurrency on
  platform_credentials.updated_at) to guarantee two concurrent hosted
  requests can never both *persist* a refreshed token for the same
  connection — one write always wins, the other loses its CAS and retries
  by re-reading (bounded by _MAX_CAS_ATTEMPTS). What this does NOT fully
  prevent: if two requests are refreshing at nearly the same instant, both
  may call TikTok's refresh endpoint with the SAME (soon-to-be-rotated)
  refresh_token before either has persisted a result — TikTok's own
  rotation behavior may then reject the second real HTTP call outright
  (a TikTokReauthorizationRequiredError a human would have to resolve by
  reconnecting), not just lose a local race. A real distributed lock
  (e.g. a Postgres advisory lock keyed by platform_connection_id) would
  close this fully; not implemented this milestone — the CAS approach
  above already fully closes the *data-integrity* risk (no double-
  persisted/corrupted credential row is possible), and true simultaneous
  refresh requests for one connection are expected to be rare in practice
  (this is a per-user, per-platform credential, not a shared global one).

Dependencies:
  cryptography.fernet. content_automation.persistence.protocol.ContentStoreProtocol.
  content_automation.publishing.tiktok.auth (token refresh only).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

from content_automation.config import CREDENTIAL_ENCRYPTION_KEY, TIKTOK_TOKEN_REFRESH_SKEW_SECONDS
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.tiktok import auth as tiktok_auth

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
    DB-backed. See the module docstring's "Concurrency" section for the
    CAS retry loop's guarantees and known residual limitation."""
    for _ in range(_MAX_CAS_ATTEMPTS):
        record = store.get_platform_credential(platform_connection_id)
        if record is None:
            raise tiktok_auth.TikTokReauthorizationRequiredError(
                "No TikTok credential saved for this connection. Connect TikTok again."
            )
        token = decrypt_token(record.encrypted_payload)
        now = datetime.now(timezone.utc)
        if _token_still_valid(token, now):
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
            return refreshed["access_token"]
        # Lost the race — another request already refreshed (and
        # persisted) first; re-read and re-check freshness rather than
        # trusting our own already-superseded refresh result.

    raise CredentialStoreError(
        f"Could not refresh the TikTok credential for connection {platform_connection_id} after "
        f"{_MAX_CAS_ATTEMPTS} attempts — repeated concurrent refresh contention."
    )
