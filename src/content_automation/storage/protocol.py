"""
protocol.py — StorageProtocol: the shared contract
storage.local.LocalStorage and storage.supabase_storage.SupabaseStorage both
satisfy (Milestone 3.4).

What it does:
  A typing.Protocol, not a base class — the same structural-typing pattern
  persistence.protocol.ContentStoreProtocol already established for
  ContentStore/PostgresContentStore in Milestone 3.3. Neither storage
  implementation inherits from this or from each other.

  Deliberately a small, four-method contract — "how do I store bytes, how
  do I get a local path for tools that require one, how do I remove an
  object, how do I check it exists" — not a general-purpose filesystem
  abstraction. See docs/decisions/0009-object-storage-media-lifecycle.md
  "Storage Contract" for why this shape and not a larger one.

Dependencies:
  stdlib typing/contextlib/pathlib only.
"""

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol


class StorageProtocol(Protocol):
    provider_name: str
    """Short identifier ("local", "supabase") stamped onto
    videos.storage_provider by media.media_storage.upload_canonical_media —
    never hardcoded by a caller, always read from the storage instance
    actually used, so the DB row always names the backend that really
    holds the bytes."""

    def put(self, key: str, local_path: Path) -> None:
        """Upload the file at local_path to the object identified by key,
        overwriting any existing object at that key (upsert — see
        media.media_storage's deterministic-key idempotency note for why
        overwrite, not append/reject, is the correct behavior here)."""
        ...

    def exists(self, key: str) -> bool:
        """True if an object exists at key."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object at key. Idempotent — deleting a key that does
        not exist is not an error (matches this codebase's existing
        "never raise merely because the end state is already reached"
        convention, e.g. ContentStore.claim_platform_post)."""
        ...

    def materialize(self, key: str) -> AbstractContextManager[Path]:
        """Context manager yielding a real local filesystem Path containing
        key's bytes, for tools that require one (ffprobe, ffmpeg,
        faster-whisper, TikTokPublisher's video_path.open()). The path is
        only guaranteed to exist for the duration of the `with` block —
        implementations clean up any temporary file on exit, success or
        exception. Raises if key does not exist; never returns a path to
        nothing.

        Usage:
            with storage.materialize(key) as path:
                inspect_media(path)
        """
        ...
