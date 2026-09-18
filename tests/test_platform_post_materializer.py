"""Unit tests for platform-post materialization (Milestone 2.1.2):
platform_post_materializer.materialize_platform_posts_for_assignment() +
ContentStore.insert_platform_post_if_missing(). Mirrors
tests/test_slot_matcher.py's style: a real (temp-file) ContentStore, a
fixed NOW, no wall-clock dependence."""

from datetime import datetime

import pytest

from content_automation.scheduling import platform_post_materializer as ppm
from content_automation.persistence.content_store import ContentStore

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture(autouse=True)
def one_platform(monkeypatch):
    """Isolate every test from whatever config.TARGET_PUBLISHING_PLATFORMS
    actually resolves to in the real environment."""
    monkeypatch.setattr(ppm, "TARGET_PUBLISHING_PLATFORMS", ["tiktok"])


def _assigned_video_and_slot(store, *, scheduled_at="2026-09-20T09:00:00"):
    store.insert_slot_if_missing(scheduled_at, "building", "prompt text", NOW.isoformat())
    slot_row = store._conn.execute(
        "SELECT id FROM content_slots WHERE scheduled_at = ?", (scheduled_at,)
    ).fetchone()
    video = store.insert_video("hash-1", "video.mp4", "/incoming/video.mp4", NOW.isoformat())
    store.assign_slot(video.id, slot_row["id"])
    return video.id, slot_row["id"]


# 1 & 4. assigning a video to a slot materializes a TikTok platform_post with correct video_id/platform
def test_materializes_platform_post_with_correct_video_and_platform(store):
    video_id, slot_id = _assigned_video_and_slot(store)

    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())

    post = store.get_platform_post(video_id, "tiktok")
    assert post is not None
    assert post.video_id == video_id
    assert post.platform == "tiktok"


# 2. new row status is PENDING
def test_new_row_status_is_pending(store):
    video_id, slot_id = _assigned_video_and_slot(store)

    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())

    post = store.get_platform_post(video_id, "tiktok")
    assert post.status == "PENDING"
    assert post.platform_post_id is None


# 3. scheduled_at exactly matches assigned content_slot
def test_scheduled_at_exactly_matches_content_slot(store):
    video_id, slot_id = _assigned_video_and_slot(store, scheduled_at="2026-09-21T09:00:00")

    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())

    post = store.get_platform_post(video_id, "tiktok")
    slot = store.get_slot(slot_id)
    assert post.scheduled_at == slot.scheduled_at == "2026-09-21T09:00:00"


# 5. repeated materialization does not create duplicates
def test_repeated_materialization_does_not_duplicate(store):
    video_id, slot_id = _assigned_video_and_slot(store)

    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())
    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())

    rows = store._conn.execute(
        "SELECT COUNT(*) AS n FROM platform_posts WHERE video_id = ? AND platform = 'tiktok'", (video_id,)
    ).fetchone()
    assert rows["n"] == 1


@pytest.mark.parametrize("existing_status", ["PUBLISHED", "PUBLISHING", "FAILED"])
def test_repeated_materialization_does_not_reset_existing_state(store, existing_status):
    """6, 7, 8: repeated materialization must not reset an existing
    PUBLISHED/PUBLISHING/FAILED row's status, platform_post_id,
    published_at, or failure_reason."""
    video_id, slot_id = _assigned_video_and_slot(store)
    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())
    post = store.get_platform_post(video_id, "tiktok")
    store.update_platform_post(
        post.id,
        updated_at=NOW.isoformat(),
        status=existing_status,
        platform_post_id="real_publish_id_123",
        published_at=NOW.isoformat() if existing_status == "PUBLISHED" else None,
        failure_reason="some real failure" if existing_status == "FAILED" else None,
    )
    before = store.get_platform_post(video_id, "tiktok")

    ppm.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at=NOW.isoformat())

    after = store.get_platform_post(video_id, "tiktok")
    assert after == before
    assert after.status == existing_status
    assert after.platform_post_id == "real_publish_id_123"


def test_unknown_slot_id_raises(store):
    video = store.insert_video("hash-x", "video.mp4", "/incoming/video.mp4", NOW.isoformat())
    with pytest.raises(ValueError, match="content_slot"):
        ppm.materialize_platform_posts_for_assignment(store, video.id, 99999, created_at=NOW.isoformat())
