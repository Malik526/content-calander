"""
identity.py — which Instagram account a hosted connection is authorized as
(Milestone 4.1).

What it does:
  fetch_instagram_username(store, platform_connection_id) asks Instagram's
  GET /me?fields=user_id,username with that connection's stored credential
  (publishing/instagram/credential_store.py — the credential-backed
  identity seam from ADR-0018 Decision 6) and returns the username only.
  The Settings card shows it as "@username"; the numeric Instagram id is
  stored as platform_connections.external_account_id and never shown.
  Usernames are not persisted anywhere — they are read live, like TikTok's
  creator_info (publishing/tiktok/creator_identity.py).

  Best-effort, never authoritative about the connection itself: any failure
  (credential missing or undecryptable, token expired, refresh rejected,
  Instagram unreachable, malformed or missing username) returns None and is
  logged with a code only. The caller then reports the connection without
  a label — it never invents one. Token values and Instagram responses are
  never logged or returned.

Dependencies:
  publishing.instagram.credential_store, publishing.instagram.oauth,
  publishing.credential_encryption.
"""

import logging

from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.credential_encryption import CredentialStoreError
from content_automation.publishing.instagram import oauth as instagram_oauth
from content_automation.publishing.instagram.credential_store import get_hosted_instagram_access_token
from content_automation.publishing.instagram.oauth import InstagramAuthError

logger = logging.getLogger(__name__)

# Runs inside a Settings status request: a slow Instagram must not hang the
# page, it just shows no name.
_LOOKUP_TIMEOUT_SECONDS = 5


def fetch_instagram_username(store: ContentStoreProtocol, platform_connection_id: int) -> str | None:
    """The connected account's Instagram username, or None when it can't
    be determined right now (see module docstring)."""
    try:
        access_token = get_hosted_instagram_access_token(store, platform_connection_id)
        profile = instagram_oauth.fetch_account_profile(access_token, timeout=_LOOKUP_TIMEOUT_SECONDS)
    except InstagramAuthError as exc:
        _log_failure(platform_connection_id, f"failure_code={exc.reason_code}")
        return None
    except CredentialStoreError:
        _log_failure(platform_connection_id, "failure_code=CREDENTIAL_UNAVAILABLE")
        return None
    except Exception as exc:  # noqa: BLE001 — a display nicety must never fail the status request
        _log_failure(platform_connection_id, f"error_type={type(exc).__name__}")
        return None
    return profile.username


def _log_failure(platform_connection_id: int, detail: str) -> None:
    logger.info(f"event=instagram_identity_failed platform_connection_id={platform_connection_id} {detail}")
