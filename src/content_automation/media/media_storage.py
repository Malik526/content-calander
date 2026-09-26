"""
media_storage.py — the domain-level bridge between a video's DB row and the
object-storage backend that may hold its canonical media (Milestone 3.4).

What it does:
  Two operations:

    upload_canonical_media(store, storage, video_id, user_id)
      Uploads the video's current canonical_media_path (a real local file
      from the existing ingestion pipeline — media/processing.py is
      completely unchanged by this milestone) to `storage`, and stamps
      storage_provider/storage_key on the video row. Additive: the
      existing local file is never deleted, and canonical_media_path is
      never cleared — see docs/decisions/0009-object-storage-media-lifecycle.md
      "Retention" for why keeping the local source indefinitely was the
      deliberate V1 choice, not an oversight.

    materialize_canonical_media(store, storage, video_id, user_id)
      Context manager yielding a real local Path to the video's canonical
      media, regardless of where it actually lives: if the video has never
      been uploaded to object storage (storage_provider is NULL — every
      video that existed before this milestone, and any new one that
      hasn't been migrated), yields canonical_media_path directly with no
      network call, exactly matching pre-3.4 behavior. If it has,
      materializes through `storage` instead. Callers (scheduling/
      publish_tiktok.py) never need to know which case applies.

  Both functions verify the video belongs to `user_id` before touching
  anything (Phase 19 — tenant isolation: a caller must never be able to
  resolve/upload/materialize another user's media even with a known
  video_id/storage key) — raises MediaOwnershipError otherwise, the same
  "check before act" shape ContentStore.assign_slot's OwnershipMismatchError
  established in Milestone 3.2.

  Storage key format: users/<user_id>/videos/<video_id>/source<ext> —
  deterministic (not a random UUID), so re-running upload_canonical_media
  for the same video is naturally idempotent at the storage layer (the
  same key is overwritten, not duplicated — see StorageProtocol.put's
  upsert contract) — see Phase 28 of the Milestone 3.4 brief ("document
  the current protection... do not create an expensive locking subsystem
  unless evidence requires one").

Dependencies:
  content_automation.persistence.content_store (ContentStore, VideoRecord),
  content_automation.persistence.protocol (ContentStoreProtocol — used only
  by create_video_from_upload, which must work against either backend
  since it is called from the hosted API; the pre-existing functions above
  keep their original ContentStore-only type hint, unchanged),
  content_automation.storage.protocol (StorageProtocol).
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from content_automation.persistence.content_store import ContentStore, VideoRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.storage.protocol import StorageProtocol


class MediaOwnershipError(Exception):
    """Raised when video_id does not belong to user_id — see module docstring."""


class MediaNotUploadedError(Exception):
    """Raised by upload_canonical_media if the video has no local
    canonical_media_path to upload yet (still mid-ingestion), and by any
    caller that needs storage-backed media for a video that was never
    migrated and has no legacy local file either."""


class DuplicateVideoContentError(Exception):
    """Raised by create_video_from_upload when file_hash already belongs to
    a *different* user (or a legacy/unowned row) — videos.file_hash is
    globally UNIQUE (predates Milestone 3.2 ownership; never rescoped to
    per-user, see docs/decisions/0009-object-storage-media-lifecycle.md's
    own note on schema changes being out of scope), so this is a real,
    expected outcome a multi-tenant upload has to handle, not a bug to fix
    by loosening the constraint. Never reveals which other user/video holds
    the content — see api/routes/videos.py for the caller-facing message."""


def build_storage_key(user_id: int, video_id: int, suffix: str) -> str:
    """users/<user_id>/videos/<video_id>/source<ext> — tenant-scoped,
    deterministic, unique per video, no user-provided filename or secret
    information embedded. See module docstring "Storage key format"."""
    return f"users/{user_id}/videos/{video_id}/source{suffix}"


def _get_owned_video(store: ContentStore, video_id: int, user_id: int) -> VideoRecord:
    video = store.get_video(video_id)
    if video is None:
        raise MediaOwnershipError(f"No video with id={video_id}.")
    if video.user_id != user_id:
        raise MediaOwnershipError(
            f"video {video_id} (user_id={video.user_id}) does not belong to user_id={user_id}."
        )
    return video


def upload_canonical_media(store: ContentStore, storage: StorageProtocol, video_id: int, user_id: int) -> VideoRecord:
    """Upload video_id's current canonical_media_path to `storage` and
    stamp storage_provider/storage_key on its row. Raises
    MediaOwnershipError if video_id does not belong to user_id;
    MediaNotUploadedError if the video has no local canonical_media_path
    yet. Idempotent — safe to call more than once for the same video (the
    deterministic key is simply overwritten with the same bytes)."""
    video = _get_owned_video(store, video_id, user_id)
    if not video.canonical_media_path:
        raise MediaNotUploadedError(f"video {video_id} has no canonical_media_path to upload yet.")

    local_path = Path(video.canonical_media_path)
    if not local_path.exists():
        raise MediaNotUploadedError(f"video {video_id}'s canonical_media_path does not exist: {local_path}")

    key = build_storage_key(user_id, video_id, local_path.suffix)
    storage.put(key, local_path)
    store.update_video(video_id, storage_provider=storage.provider_name, storage_key=key)
    return store.get_video(video_id)


def create_video_from_upload(
    store: ContentStoreProtocol,
    storage: StorageProtocol,
    user_id: int,
    local_path: Path,
    original_filename: str,
    file_hash: str,
    file_size_bytes: int,
    created_at: str,
) -> VideoRecord:
    """The hosted-batch-upload counterpart to upload_canonical_media
    (Milestone 3.7): creates a brand-new owned videos row directly from
    already-received bytes at local_path (a temp file the caller wrote from
    an HTTP upload — api/routes/videos.py), rather than migrating a video
    that already exists from local CLI ingestion. canonical_media_path is
    deliberately left NULL, never set to local_path — that temp file does
    not survive past the request (unlike the local ingestion pipeline's
    permanently-retained content/processed/ copy, see ADR-0009
    "Retention"), so storage_provider/storage_key are the only reference
    this row ever has, exactly the "migrated" shape
    materialize_canonical_media already knows how to resolve. status stays
    the schema default ('DISCOVERED') — no transcription/classification/
    scheduling happens here; this function's only job is "bytes are safely
    stored and there is an owned DB row for them" (see this milestone's own
    scope guardrail).

    Idempotent per (user, exact content): if file_hash already belongs to
    this same user, returns the existing row unchanged rather than creating
    a duplicate — re-uploading the same file (e.g. a retried batch) is not
    an error. Raises DuplicateVideoContentError if file_hash belongs to a
    *different* user, or to a legacy/unowned row — videos.file_hash is
    globally UNIQUE at the schema level (never rescoped to per-user; see
    that error's own docstring), so this is the one collision a multi-
    tenant caller must always be ready to handle."""
    existing = store.get_video_by_hash(file_hash)
    if existing is not None:
        if existing.user_id == user_id:
            return existing
        raise DuplicateVideoContentError(f"file_hash={file_hash!r} is already associated with a different account.")

    # Milestone 3.7 has no local-discovery-directory concept at all for a
    # hosted upload — original_path only has to be unique-in-practice and
    # obviously not a real filesystem path (get_video_by_path, used solely
    # by the local CLI's discover_videos FIFO ordering, must never
    # accidentally match one of these). No user-provided filename or
    # secret information embedded, matching build_storage_key's own
    # convention above.
    original_path = f"hosted-upload/{file_hash}"
    video = store.insert_video(
        file_hash=file_hash, original_filename=original_filename, original_path=original_path,
        created_at=created_at, user_id=user_id,
    )
    key = build_storage_key(user_id, video.id, Path(original_filename).suffix)
    storage.put(key, local_path)
    store.update_video(video.id, storage_provider=storage.provider_name, storage_key=key, file_size_bytes=file_size_bytes)
    return store.get_video(video.id)


@contextmanager
def materialize_canonical_media(
    store: ContentStore, storage: StorageProtocol, video_id: int, user_id: int
) -> Iterator[Path]:
    """Yield a real local Path to video_id's canonical media — via
    `storage` if it has been uploaded (storage_provider set), or directly
    from canonical_media_path (no network call) if not. See module
    docstring."""
    video = _get_owned_video(store, video_id, user_id)

    if video.storage_provider and video.storage_key:
        with storage.materialize(video.storage_key) as path:
            yield path
        return

    if not video.canonical_media_path:
        raise MediaNotUploadedError(f"video {video_id} has no canonical_media_path and was never uploaded to storage.")
    yield Path(video.canonical_media_path)
