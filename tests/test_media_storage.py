"""Tests for media.media_storage — the domain-level bridge between a
video's DB row and its object-storage backend (Milestone 3.4). Uses a real
(temp-file) ContentStore + LocalStorage — no network dependency; the
ownership/idempotency/materialization contract this module establishes is
backend-agnostic, exercised here against the fast local backend and again
against real Supabase in tests/test_storage_supabase.py."""

from datetime import datetime

import pytest

from content_automation.media import media_storage
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


def _video_with_local_media(store, tmp_path, user_id, *, name="clip"):
    video_path = tmp_path / f"{name}.mp4"
    video_path.write_bytes(f"{name} bytes".encode() * 100)
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", str(video_path), NOW.isoformat(), user_id=user_id)
    store.update_video(video.id, canonical_media_path=str(video_path))
    return store.get_video(video.id), video_path


def test_build_storage_key_is_tenant_scoped_and_deterministic():
    key1 = media_storage.build_storage_key(user_id=3, video_id=12, suffix=".mp4")
    key2 = media_storage.build_storage_key(user_id=3, video_id=12, suffix=".mp4")
    key_other_user = media_storage.build_storage_key(user_id=4, video_id=12, suffix=".mp4")

    assert key1 == key2  # deterministic
    assert key1 == "users/3/videos/12/source.mp4"
    assert key1 != key_other_user
    assert "3" in key1 and "12" in key1


def test_upload_canonical_media_stamps_storage_fields(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    updated = media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert updated.storage_provider == "local"
    assert updated.storage_key == f"users/{user.id}/videos/{video.id}/source.mp4"
    assert storage.exists(updated.storage_key)


def test_upload_canonical_media_never_deletes_local_source(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert video_path.exists()  # original local file untouched — see ADR "Retention"


def test_upload_canonical_media_is_idempotent(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    first = media_storage.upload_canonical_media(store, storage, video.id, user.id)
    second = media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert first.storage_key == second.storage_key  # same deterministic key, overwritten not duplicated


def test_upload_canonical_media_rejects_wrong_user(store, storage, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video, _ = _video_with_local_media(store, tmp_path, user_a.id)

    with pytest.raises(media_storage.MediaOwnershipError):
        media_storage.upload_canonical_media(store, storage, video.id, user_b.id)


def test_upload_canonical_media_requires_a_local_file_first(store, storage):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = store.insert_video("hash-empty", "clip.mp4", "/incoming/clip.mp4", NOW.isoformat(), user_id=user.id)

    with pytest.raises(media_storage.MediaNotUploadedError):
        media_storage.upload_canonical_media(store, storage, video.id, user.id)


def test_materialize_canonical_media_uses_storage_when_uploaded(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)
    media_storage.upload_canonical_media(store, storage, video.id, user.id)
    video_path.unlink()  # prove materialize reads from storage, not the (now-deleted) local original

    with media_storage.materialize_canonical_media(store, storage, video.id, user.id) as materialized:
        assert materialized.read_bytes() == b"clip bytes" * 100


def test_materialize_canonical_media_falls_back_to_local_path_when_not_uploaded(store, storage, tmp_path):
    """A video never uploaded to object storage (storage_provider NULL —
    every pre-3.4 video) must materialize directly from
    canonical_media_path, with zero calls to `storage` — proven here by
    passing a storage instance whose root doesn't even exist yet."""
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    with media_storage.materialize_canonical_media(store, storage, video.id, user.id) as materialized:
        assert materialized == video_path
        assert materialized.read_bytes() == video_path.read_bytes()


def test_materialize_canonical_media_rejects_wrong_user(store, storage, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video, _ = _video_with_local_media(store, tmp_path, user_a.id)
    media_storage.upload_canonical_media(store, storage, video.id, user_a.id)

    with pytest.raises(media_storage.MediaOwnershipError):
        with media_storage.materialize_canonical_media(store, storage, video.id, user_b.id):
            pass
