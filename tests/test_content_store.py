"""Idempotency tests for content_store.py."""

import pytest

from content_store import ContentStore


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def test_insert_slot_if_missing_is_idempotent(store):
    """Re-running generate_calendar.py for the same month must not duplicate slots."""
    created_first = store.insert_slot_if_missing(
        "2026-09-14T10:00:00", "building", "prompt A", "2026-09-01T00:00:00"
    )
    created_second = store.insert_slot_if_missing(
        "2026-09-14T10:00:00", "building", "prompt A (regenerated)", "2026-09-02T00:00:00"
    )

    assert created_first is True
    assert created_second is False

    slot = store.find_earliest_open_slot("building", "2000-01-01T00:00:00")
    assert slot.prompt == "prompt A"  # first insert wins; rerun did not overwrite it


def test_get_video_by_hash_returns_none_when_absent(store):
    assert store.get_video_by_hash("does-not-exist") is None


def test_insert_video_then_lookup_by_hash_is_idempotent_identity(store):
    """process_content.py calls get_video_by_hash before insert_video on every run;
    the same physical file (same hash) must resolve to the same DB row."""
    created = store.insert_video("abc123", "video.mp4", "/incoming/video.mp4", "2026-09-01T00:00:00")

    found = store.get_video_by_hash("abc123")

    assert found.id == created.id
    assert found.status == "DISCOVERED"


def test_update_video_persists_fields(store):
    video = store.insert_video("abc123", "video.mp4", "/incoming/video.mp4", "2026-09-01T00:00:00")

    store.update_video(video.id, status="VALIDATED", container="mov", video_codec="hevc")

    updated = store.get_video_by_hash("abc123")
    assert updated.status == "VALIDATED"
    assert updated.container == "mov"
    assert updated.video_codec == "hevc"
