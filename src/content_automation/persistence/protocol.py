"""
protocol.py — ContentStoreProtocol: the shared method-surface contract
persistence.content_store.ContentStore (SQLite) and
persistence.postgres_content_store.PostgresContentStore both satisfy
(Milestone 3.3).

What it does:
  A typing.Protocol, not a base class — structural, not nominal, typing.
  Neither ContentStore nor PostgresContentStore inherits from this or from
  each other; each already satisfies it by having the matching methods.
  This exists purely as documentation-as-code of the shared contract (per
  the Milestone 3.3 brief's own Phase 5: "If introducing an explicit
  persistence protocol/interface is now justified because there are
  genuinely two backends, this is the milestone where it may become
  appropriate") — it changes nothing about either concrete class and
  requires no change to any existing caller. A module that wants to accept
  "either backend" can type-hint a parameter as ContentStoreProtocol instead
  of a concrete class; nothing in this codebase is required to do so.

  Deliberately does not enumerate every method either store has — only the
  ones both share (the two backends diverge on ownership optionality; see
  docs/decisions/0008-postgres-persistence-migration.md "ContentStore
  Contract" for exactly which methods diverge and why).

Dependencies:
  stdlib typing only.
"""

from typing import Protocol

from content_automation.persistence.content_store import (
    AuthIdentityRecord,
    OAuthStateRecord,
    PlatformConnectionRecord,
    PlatformCredentialRecord,
    PlatformPostRecord,
    SlotRecord,
    UploadAttemptRecord,
    UploadBatchRecord,
    UserRecord,
    VideoRecord,
)


class ContentStoreProtocol(Protocol):
    def close(self) -> None: ...

    def __enter__(self) -> "ContentStoreProtocol": ...

    def __exit__(self, *exc_info) -> None: ...

    def get_or_create_local_user(self) -> UserRecord: ...

    def get_video(self, video_id: int) -> VideoRecord | None: ...

    def get_video_by_hash(self, file_hash: str) -> VideoRecord | None: ...

    def update_video(self, video_id: int, **fields) -> None: ...

    # -- Milestone 3.7 (batch upload) — the methods media/media_storage.py's
    # hosted-upload path and api/routes/videos.py consume through this
    # Protocol, exactly like the Milestone 3.6 methods below.

    def insert_video(
        self, file_hash: str, original_filename: str, original_path: str, created_at: str, user_id: int,
    ) -> VideoRecord: ...

    def list_videos_for_user(self, user_id: int) -> list[VideoRecord]: ...

    def get_slot(self, slot_id: int) -> SlotRecord | None: ...

    def assign_slot(self, video_id: int, slot_id: int) -> None: ...

    def get_platform_post(self, video_id: int, platform: str) -> PlatformPostRecord | None: ...

    def update_platform_post(self, post_id: int, updated_at: str, **fields) -> None: ...

    # -- Milestone 3.6 (real authentication + hosted TikTok connection) —
    # the methods api/ and cli/link_bootstrap_user.py consume through this
    # Protocol rather than a concrete store class, so either backend works
    # unchanged.

    def get_user(self, user_id: int) -> UserRecord | None: ...

    def get_user_by_email(self, email: str) -> UserRecord | None: ...

    def create_user(self, email: str, display_name: str | None, created_at: str) -> UserRecord: ...

    def get_user_by_auth_identity(self, provider: str, provider_subject: str) -> UserRecord | None: ...

    def create_auth_identity(
        self, user_id: int, provider: str, provider_subject: str, provider_email: str | None, created_at: str,
    ) -> AuthIdentityRecord: ...

    def get_platform_connection(self, user_id: int, platform: str) -> PlatformConnectionRecord | None: ...

    def get_or_create_platform_connection(
        self, user_id: int, platform: str, external_account_id: str | None = None,
    ) -> PlatformConnectionRecord: ...

    def update_platform_connection_status(self, connection_id: int, status: str, updated_at: str) -> None: ...

    def get_platform_credential(self, platform_connection_id: int) -> PlatformCredentialRecord | None: ...

    def upsert_platform_credential(
        self, platform_connection_id: int, encrypted_payload: str, now: str,
    ) -> PlatformCredentialRecord: ...

    def update_platform_credential_if_unchanged(
        self, platform_connection_id: int, encrypted_payload: str, expected_updated_at: str, new_updated_at: str,
    ) -> bool: ...

    def delete_platform_credential(self, platform_connection_id: int) -> None: ...

    def create_oauth_state(
        self, user_id: int, platform: str, state: str, code_verifier: str, redirect_uri: str,
        created_at: str, expires_at: str,
    ) -> OAuthStateRecord: ...

    def consume_oauth_state(self, state: str, now: str) -> OAuthStateRecord | None: ...

    # -- Milestone 3.7 follow-up (upload performance instrumentation) —
    # the methods api/routes/videos.py consumes through this Protocol,
    # exactly like the Milestone 3.6/3.7 methods above.

    def create_upload_batch(self, user_id: int, started_at: str, file_count: int) -> UploadBatchRecord: ...

    def update_upload_batch(self, batch_id: int, **fields) -> None: ...

    def list_upload_batches_for_user(self, user_id: int) -> list[UploadBatchRecord]: ...

    def create_upload_attempt(
        self, batch_id: int, user_id: int, original_filename: str, started_at: str,
    ) -> UploadAttemptRecord: ...

    def update_upload_attempt(self, attempt_id: int, **fields) -> None: ...

    def get_upload_attempts_for_batch(self, batch_id: int) -> list[UploadAttemptRecord]: ...
