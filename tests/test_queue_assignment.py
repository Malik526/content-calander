"""Unit tests for scheduling/queue_assignment.py (Milestone 3.9: Queue +
Calendar Functionality) — manual and automatic/FIFO video-to-slot
assignment glue. Mirrors tests/test_slot_matcher.py's and
tests/test_platform_post_materializer.py's style: a real (temp-file)
ContentStore, a fixed NOW, no wall-clock dependence."""

from datetime import datetime

import pytest

from content_automation.persistence.content_store import ContentStore, SlotUnavailableError
from content_automation.scheduling import platform_post_materializer as ppm
from content_automation.scheduling import queue_assignment as qa

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture(autouse=True)
def one_platform(monkeypatch):
    """Isolate every test from whatever config.TARGET_PUBLISHING_PLATFORMS
    actually resolves to in the real environment — same pattern
    tests/test_platform_post_materializer.py already established (it
    patches the name platform_post_materializer.py imported at module
    load time, not content_automation.config itself)."""
    monkeypatch.setattr(ppm, "TARGET_PUBLISHING_PLATFORMS", ["tiktok"])


def _user(store, email="a@example.com"):
    return store.create_user(email, "Creator", NOW.isoformat())


def _video(store, user, *, name="v"):
    return store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat(), user_id=user.id)


def _open_slot(store, user, *, scheduled_at):
    """Creates (or reuses) the slot at exactly scheduled_at and returns it
    by that identity — deliberately not "the earliest OPEN slot for this
    user," since a test that creates several slots before assigning any of
    them would otherwise get the same (earliest) slot back every time."""
    store.insert_slot_if_missing(scheduled_at, None, None, NOW.isoformat(), user_id=user.id)
    row = store._conn.execute(
        "SELECT id FROM content_slots WHERE user_id = ? AND scheduled_at = ?", (user.id, scheduled_at)
    ).fetchone()
    return store.get_slot(row["id"])


# ---------------------------------------------------------------------------
# assign_video_to_slot (manual)
# ---------------------------------------------------------------------------

def test_assign_video_to_slot_claims_and_materializes(store):
    user = _user(store)
    video = _video(store, user)
    slot = _open_slot(store, user, scheduled_at="2026-09-20T09:00:00")

    result = qa.assign_video_to_slot(store, video.id, slot.id, NOW.isoformat(), user_id=user.id)

    assert result.status == "ASSIGNED"
    assert result.assigned_video_id == video.id
    assert store.get_video(video.id).assigned_slot_id == slot.id
    post = store.get_platform_post(video.id, "tiktok")
    assert post is not None
    assert post.status == "PENDING"
    assert post.scheduled_at == slot.scheduled_at


def test_assign_video_to_slot_reraises_slot_unavailable(store):
    user = _user(store)
    video_a = _video(store, user, name="a")
    video_b = _video(store, user, name="b")
    slot = _open_slot(store, user, scheduled_at="2026-09-20T09:00:00")
    qa.assign_video_to_slot(store, video_a.id, slot.id, NOW.isoformat(), user_id=user.id)

    with pytest.raises(SlotUnavailableError):
        qa.assign_video_to_slot(store, video_b.id, slot.id, NOW.isoformat(), user_id=user.id)


def test_assign_video_to_slot_refuses_a_video_already_scheduled_elsewhere(store):
    user = _user(store)
    video = _video(store, user)
    first_slot = _open_slot(store, user, scheduled_at="2026-09-20T09:00:00")
    second_slot = _open_slot(store, user, scheduled_at="2026-09-21T09:00:00")
    qa.assign_video_to_slot(store, video.id, first_slot.id, NOW.isoformat(), user_id=user.id)

    with pytest.raises(qa.VideoAlreadyScheduledError):
        qa.assign_video_to_slot(store, video.id, second_slot.id, NOW.isoformat(), user_id=user.id)

    # nothing about the first, real assignment changed
    assert store.get_video(video.id).assigned_slot_id == first_slot.id
    assert store.get_slot(second_slot.id).status == "OPEN"


# ---------------------------------------------------------------------------
# assign_video_to_next_open_slot (automatic/FIFO)
# ---------------------------------------------------------------------------

def test_assign_video_to_next_open_slot_picks_the_earliest_one(store):
    user = _user(store)
    video = _video(store, user)
    _open_slot(store, user, scheduled_at="2026-09-22T09:00:00")
    earliest = _open_slot(store, user, scheduled_at="2026-09-20T09:00:00")
    _open_slot(store, user, scheduled_at="2026-09-21T09:00:00")

    result = qa.assign_video_to_next_open_slot(store, video.id, NOW.isoformat(), user_id=user.id, now=NOW)

    assert result.id == earliest.id
    assert result.status == "ASSIGNED"


def test_assign_video_to_next_open_slot_raises_when_none_available(store):
    user = _user(store)
    video = _video(store, user)

    with pytest.raises(qa.NoOpenSlotAvailableError):
        qa.assign_video_to_next_open_slot(store, video.id, NOW.isoformat(), user_id=user.id, now=NOW)


def test_assign_video_to_next_open_slot_refuses_a_video_already_scheduled(store):
    user = _user(store)
    video = _video(store, user)
    first_slot = _open_slot(store, user, scheduled_at="2026-09-20T09:00:00")
    _open_slot(store, user, scheduled_at="2026-09-21T09:00:00")
    qa.assign_video_to_slot(store, video.id, first_slot.id, NOW.isoformat(), user_id=user.id)

    with pytest.raises(qa.VideoAlreadyScheduledError):
        qa.assign_video_to_next_open_slot(store, video.id, NOW.isoformat(), user_id=user.id, now=NOW)
