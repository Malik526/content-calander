"""Unit tests for deterministic slot selection (slot_matcher.py + content_store.py)."""

from datetime import datetime, timedelta

import pytest

import slot_matcher
from content_store import ContentStore, SlotUnavailableError


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


NOW = datetime(2026, 9, 14, 8, 0, 0)


def _add_slot(store, *, days_from_now, pillar="building", status="OPEN"):
    scheduled_at = (NOW + timedelta(days=days_from_now)).isoformat()
    store.insert_slot_if_missing(scheduled_at, pillar, "prompt text", NOW.isoformat())
    if status != "OPEN":
        row = store._conn.execute(
            "SELECT id FROM content_slots WHERE scheduled_at = ? AND pillar_key = ?",
            (scheduled_at, pillar),
        ).fetchone()
        store._conn.execute("UPDATE content_slots SET status = ? WHERE id = ?", (status, row["id"]))
    return scheduled_at


def test_one_matching_slot(store):
    _add_slot(store, days_from_now=3)

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot is not None
    assert slot.pillar_key == "building"


def test_multiple_matching_slots_picks_earliest(store):
    _add_slot(store, days_from_now=10)
    _add_slot(store, days_from_now=2)
    _add_slot(store, days_from_now=5)

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot.scheduled_at == (NOW + timedelta(days=2)).isoformat()


def test_occupied_slot_is_skipped(store):
    _add_slot(store, days_from_now=1, status="ASSIGNED")
    _add_slot(store, days_from_now=4)

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot.scheduled_at == (NOW + timedelta(days=4)).isoformat()


def test_past_slot_is_skipped(store):
    _add_slot(store, days_from_now=-1)
    _add_slot(store, days_from_now=3)

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot.scheduled_at == (NOW + timedelta(days=3)).isoformat()


def test_no_available_slot_returns_none(store):
    _add_slot(store, days_from_now=-1)
    _add_slot(store, days_from_now=2, status="ASSIGNED")

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot is None


def test_wrong_pillar_is_ignored(store):
    _add_slot(store, days_from_now=1, pillar="acquisition")

    slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert slot is None


def test_two_same_pillar_videos_get_different_slots(store):
    """Simulates process_content's sequential per-video assignment."""
    _add_slot(store, days_from_now=1)
    _add_slot(store, days_from_now=2)

    first_slot = slot_matcher.select_slot(store, "building", now=NOW)
    store.assign_slot(video_id=_insert_video(store, "video_a"), slot_id=first_slot.id)

    second_slot = slot_matcher.select_slot(store, "building", now=NOW)

    assert second_slot.id != first_slot.id
    assert second_slot.scheduled_at == (NOW + timedelta(days=2)).isoformat()


def test_assign_slot_rejects_already_assigned_slot(store):
    slot_at = _add_slot(store, days_from_now=1)
    slot = slot_matcher.select_slot(store, "building", now=NOW)
    video_a = _insert_video(store, "video_a")
    video_b = _insert_video(store, "video_b")

    store.assign_slot(video_id=video_a, slot_id=slot.id)

    with pytest.raises(SlotUnavailableError):
        store.assign_slot(video_id=video_b, slot_id=slot.id)


def _insert_video(store, name):
    record = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())
    return record.id
