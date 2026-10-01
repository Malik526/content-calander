"""
hosted_publisher.py — a TikTokPublisher that authenticates as one hosted
user's own TikTok connection (Milestone 3.12: Hosted Scheduler + Worker
Execution).

What it does:
  build_hosted_tiktok_publisher(store, user_id) returns the ordinary
  TikTokPublisher — same creator_info/init/upload/status wire protocol the
  local CLI uses — with its access token supplied by
  credential_store.get_hosted_tiktok_access_token for that user's
  platform_connections row instead of the local token file. This is what
  keeps the multi-tenant execution invariant
  (docs/architecture/hosted-product-boundary.md §5): the hosted worker
  builds one publisher per user and only ever runs that user's posts
  through it.

  The token is fetched lazily, on each TikTok call, so a missing/expired
  connection surfaces exactly where every other auth failure already does
  (TikTokPublisher._headers -> PublishError) and is classified by the
  existing retry_classification rules:
    - no connection row, a non-ACTIVE connection, or no stored credential
      -> REAUTHORIZATION_REQUIRED (terminal; "Reconnect TikTok" in the UI)
    - a credential that can't be decrypted (rotated/missing
      CREDENTIAL_ENCRYPTION_KEY) -> CREDENTIAL_UNAVAILABLE (terminal;
      reconnecting re-encrypts it under the current key)
    - a transient refresh failure keeps auth.py's own reason_code
      (e.g. NETWORK_ERROR -> retryable), unchanged.

  Token values never leave this function's return path — nothing here
  logs or stores them.

Dependencies:
  publishing.tiktok.publisher, publishing.tiktok.credential_store,
  publishing.tiktok.auth (error types), persistence.protocol.
"""

from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.tiktok.auth import TikTokAuthError, TikTokReauthorizationRequiredError
from content_automation.publishing.tiktok.credential_store import CredentialStoreError, get_hosted_tiktok_access_token
from content_automation.publishing.tiktok.publisher import TikTokPublisher

PLATFORM = "tiktok"


def hosted_access_token_provider(store: ContentStoreProtocol, user_id: int):
    """A zero-argument callable returning a valid access token for
    user_id's TikTok connection, or raising TikTokAuthError."""

    def provide() -> str:
        connection = store.get_platform_connection(user_id, PLATFORM)
        if connection is None or connection.status != "ACTIVE":
            raise TikTokReauthorizationRequiredError("TikTok is not connected for this account. Connect TikTok again.")
        try:
            return get_hosted_tiktok_access_token(store, connection.id)
        except CredentialStoreError as exc:
            raise TikTokAuthError(
                "The stored TikTok credential could not be used. Reconnect TikTok.", reason_code="CREDENTIAL_UNAVAILABLE",
            ) from exc

    return provide


def build_hosted_tiktok_publisher(store: ContentStoreProtocol, user_id: int) -> TikTokPublisher:
    return TikTokPublisher(access_token_provider=hosted_access_token_provider(store, user_id))
