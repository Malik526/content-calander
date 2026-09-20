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
    PlatformPostRecord,
    SlotRecord,
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

    def get_slot(self, slot_id: int) -> SlotRecord | None: ...

    def assign_slot(self, video_id: int, slot_id: int) -> None: ...

    def get_platform_post(self, video_id: int, platform: str) -> PlatformPostRecord | None: ...

    def update_platform_post(self, post_id: int, updated_at: str, **fields) -> None: ...
