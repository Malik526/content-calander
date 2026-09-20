"""Tests for cli/migrate_media_to_object_storage.py (Milestone 3.4). Uses a
real (temp-file) ContentStore + LocalStorage — no network dependency."""

from datetime import datetime

import pytest

import migrate_media_to_object_storage as mmos
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


def _video_with_local_file(store, tmp_path, user_id, *, name="clip", content=b"video bytes"):
    video_path = tmp_path / f"{name}.mp4"
    video_path.write_bytes(content)
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", str(video_path), NOW.isoformat(), user_id=user_id)
    store.update_video(video.id, canonical_media_path=str(video_path))
    return store.get_video(video.id), video_path


def test_migrates_video_with_local_file(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_file(store, tmp_path, user.id)

    report = mmos.migrate_media(store, storage)

    assert report == [{
        "video_id": video.id, "outcome": "migrated",
        "storage_provider": "local", "storage_key": f"users/{user.id}/videos/{video.id}/source.mp4",
        "sha256": mmos._sha256(video_path),
    }]
    assert video_path.exists()  # original never deleted


def test_verifies_hash_after_upload(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_file(store, tmp_path, user.id)

    [entry] = mmos.migrate_media(store, storage)

    with storage.materialize(entry["storage_key"]) as materialized:
        assert mmos._sha256(materialized) == entry["sha256"]


def test_skips_already_migrated_video(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_file(store, tmp_path, user.id)
    mmos.migrate_media(store, storage)  # first pass migrates it

    second_report = mmos.migrate_media(store, storage)

    assert second_report == [{"video_id": video.id, "outcome": "skipped_already_migrated"}]


def test_skips_video_with_no_canonical_media_path(store, storage):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    store.insert_video("hash-empty", "clip.mp4", "/incoming/clip.mp4", NOW.isoformat(), user_id=user.id)

    report = mmos.migrate_media(store, storage)

    assert report == []  # never even considered — no canonical_media_path set


def test_skips_video_whose_local_file_no_longer_exists(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_file(store, tmp_path, user.id)
    video_path.unlink()

    report = mmos.migrate_media(store, storage)

    assert report == [{"video_id": video.id, "outcome": "skipped_no_local_file", "path": str(video_path)}]


def test_dry_run_uploads_nothing(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_file(store, tmp_path, user.id)

    report = mmos.migrate_media(store, storage, dry_run=True)

    assert report[0]["outcome"] == "would_migrate"
    assert store.get_video(video.id).storage_provider is None
    assert storage.exists(f"users/{user.id}/videos/{video.id}/source.mp4") is False


def test_multiple_videos_all_migrated_independently(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    v1, _ = _video_with_local_file(store, tmp_path, user.id, name="v1", content=b"first video")
    v2, _ = _video_with_local_file(store, tmp_path, user.id, name="v2", content=b"second video, different content")

    report = mmos.migrate_media(store, storage)

    assert {e["video_id"] for e in report} == {v1.id, v2.id}
    assert all(e["outcome"] == "migrated" for e in report)
