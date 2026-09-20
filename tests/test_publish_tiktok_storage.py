"""Tests for the Milestone 3.4 object-storage integration in
scheduling/publish_tiktok.py: execute_claimed_platform_post/publish_video
resolving media through StorageProtocol.materialize() for a storage-backed
video, instead of reading canonical_media_path directly. Uses a real
(temp-file) ContentStore + LocalStorage + FakePublisher — no network
dependency (the same contract is proven again against real Supabase in
tests/test_storage_supabase.py's end-to-end case)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from content_automation.media import media_storage
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishResult, PublishStatusResult
from content_automation.scheduling import worker
from content_automation.scheduling.publish_tiktok import PublishTikTokError, execute_claimed_platform_post, publish_video
from content_automation.storage.local import LocalStorage

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


@dataclass
class FakePublisher:
    """publish_calls records (video_path, caption, bytes_at_call_time) —
    the bytes are read *during* publish(), not left for the caller to read
    later, because a storage-materialized path is only guaranteed valid
    for the duration of execute_claimed_platform_post's own `with` block
    (see test_execute_claimed_platform_post_cleans_up_temp_file_after_publish) —
    reading it after the call returns would just be testing the temp-file
    cleanup this class doesn't need to duplicate."""
    publish_result: PublishResult = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult = field(default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE"))
    publish_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption, video_path.read_bytes()))
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        return self.status_result


def _storage_backed_video(store, storage, tmp_path, user_id, *, name="clip"):
    video_path = tmp_path / f"{name}.mp4"
    video_path.write_bytes(b"fake mp4 bytes")
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", str(video_path), NOW.isoformat(), user_id=user_id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello world", caption_source="transcript_auto", status="ASSIGNED",
    )
    media_storage.upload_canonical_media(store, storage, video.id, user_id)
    video_path.unlink()  # prove publishing reads from storage, not the (now-deleted) local original
    return store.get_video(video.id)


def test_execute_claimed_platform_post_publishes_via_storage_materialization(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _storage_backed_video(store, storage, tmp_path, user.id)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    publisher = FakePublisher()

    execute_claimed_platform_post(store, video.id, "tiktok", publisher, storage=storage)

    assert len(publisher.publish_calls) == 1
    published_path, caption, published_bytes = publisher.publish_calls[0]
    assert published_bytes == b"fake mp4 bytes"
    assert caption == "hello world"
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_execute_claimed_platform_post_cleans_up_temp_file_after_publish(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _storage_backed_video(store, storage, tmp_path, user.id)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    publisher = FakePublisher()

    execute_claimed_platform_post(store, video.id, "tiktok", publisher, storage=storage)

    published_path, _, _ = publisher.publish_calls[0]
    assert not published_path.exists()  # temp materialization cleaned up after use


def test_execute_claimed_platform_post_missing_storage_backend_fails_clearly(store, storage, tmp_path):
    """A storage-backed video, but no `storage` argument supplied — must
    fail clearly (marked FAILED) rather than silently reading a stale/
    nonexistent canonical_media_path."""
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _storage_backed_video(store, storage, tmp_path, user.id)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    publisher = FakePublisher()

    with pytest.raises(PublishTikTokError):
        execute_claimed_platform_post(store, video.id, "tiktok", publisher, storage=None)

    final = store.get_platform_post(video.id, "tiktok")
    assert final.status == "FAILED"
    assert len(publisher.publish_calls) == 0


def test_worker_publishes_storage_backed_video(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _storage_backed_video(store, storage, tmp_path, user.id)
    store.insert_platform_post(
        video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id,
        scheduled_at=(NOW - timedelta(hours=1)).isoformat(),
    )
    publisher = FakePublisher()

    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW, user_id=user.id, storage=storage)

    assert summary.published == 1
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_legacy_local_video_unaffected_by_storage_parameter(store, tmp_path):
    """A video that was never uploaded to object storage (storage_provider
    NULL) must behave identically whether or not a `storage` argument is
    supplied — the pre-3.4 behavior this milestone must not change."""
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video_path = tmp_path / "legacy.mp4"
    video_path.write_bytes(b"legacy bytes")
    video = store.insert_video("hash-legacy", "legacy.mp4", str(video_path), NOW.isoformat(), user_id=user.id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello", caption_source="transcript_auto", status="ASSIGNED",
    )
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat(), user_id=user.id)
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    publisher = FakePublisher()

    # storage=None (the default) and an arbitrary unrelated LocalStorage
    # instance must both work identically — storage_provider is NULL, so
    # neither branch ever consults `storage` at all.
    execute_claimed_platform_post(store, video.id, "tiktok", publisher, storage=None)

    published_path, _, _ = publisher.publish_calls[0]
    assert published_path == video_path
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_publish_video_manual_cli_path_works_with_storage_backed_video(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _storage_backed_video(store, storage, tmp_path, user.id)
    publisher = FakePublisher()

    publish_video(store, video.id, publisher, storage=storage)

    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"
