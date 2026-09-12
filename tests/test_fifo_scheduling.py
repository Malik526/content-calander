"""Tests for FIFO routing mode (Milestone 1.3): calendar generation produces
untyped slots without invoking pillar allocation, and slot matching/
assignment is deterministic FIFO with no pillar or classifier involvement.

Pillar-mode regression coverage for build_schedule/future-only filtering
lives in tests/test_generate_calendar.py and
tests/test_future_only_schedule.py (both updated to pass
routing_mode="pillar" explicitly now that "fifo" is the default)."""

from datetime import datetime

import pytest

import generate_calendar
import scheduling
from content_store import ContentStore
from generate_calendar import build_event_body, build_schedule
from scheduling import generate_posting_dates

PAST_BOUNDARY = datetime(2000, 1, 1)


# ---------------------------------------------------------------------------
# FIFO calendar generation
# ---------------------------------------------------------------------------

def test_build_schedule_fifo_has_no_pillar_or_prompt():
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")

    assert schedule  # sanity: default cadence produces some slots
    assert all(post.content_type is None for post in schedule)
    assert all(post.prompt is None for post in schedule)


def test_build_schedule_fifo_matches_full_future_only_date_set():
    """FIFO's WHEN is exactly generate_posting_dates + filter_future_dates —
    no allocation/reallocation logic touches the count."""
    dates = generate_posting_dates(2026, 6, 4, "auto", "09:00")
    expected = scheduling.filter_future_dates(dates, PAST_BOUNDARY)

    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")

    assert [post.scheduled_at for post in schedule] == expected


def test_build_schedule_fifo_future_only_partial_month():
    start_at = datetime(2026, 9, 12, 13, 0)
    full_dates = generate_posting_dates(2026, 9, 4, "auto", "09:00")
    future_dates = [dt for dt in full_dates if dt >= start_at]
    assert len(future_dates) < len(full_dates)  # sanity: month really is partly elapsed

    schedule = build_schedule(2026, 9, start_at=start_at, routing_mode="fifo")

    assert len(schedule) == len(future_dates)
    assert all(post.scheduled_at >= start_at for post in schedule)


def test_build_schedule_fifo_future_month_unaffected():
    start_at = datetime(2026, 9, 1, 0, 0)

    with_boundary = build_schedule(2026, 10, start_at=start_at, routing_mode="fifo")
    without_boundary = build_schedule(2026, 10, start_at=PAST_BOUNDARY, routing_mode="fifo")

    assert len(with_boundary) == len(without_boundary)


def test_build_schedule_fifo_entirely_past_month_returns_empty():
    start_at = datetime(2026, 10, 1, 0, 0)

    schedule = build_schedule(2026, 9, start_at=start_at, routing_mode="fifo")

    assert schedule == []


def test_build_schedule_fifo_never_invokes_pillar_allocator(monkeypatch):
    """Explicit check of the Definition of Done item: FIFO mode must not
    invoke the weighted pillar allocator at all."""

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("allocate_pillars must not be called in FIFO mode")

    monkeypatch.setattr(generate_calendar, "allocate_pillars", _must_not_be_called)
    monkeypatch.setattr(generate_calendar, "distribute_pillars", _must_not_be_called)

    build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")  # must not raise


def test_build_schedule_default_routing_mode_is_fifo():
    """config.ROUTING_MODE defaults to 'fifo' — build_schedule with no
    routing_mode argument must match the explicit fifo call."""
    default_schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY)
    fifo_schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")

    assert [(p.scheduled_at, p.content_type, p.prompt) for p in default_schedule] == [
        (p.scheduled_at, p.content_type, p.prompt) for p in fifo_schedule
    ]


def test_build_event_body_fifo_has_generic_title_and_no_color():
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")
    post = schedule[0]

    body = build_event_body(post)

    assert body["summary"] == "Content Post"
    assert body["description"] == ""
    assert "colorId" not in body


def test_generate_calendar_persists_fifo_slots_with_null_pillar_key(tmp_path):
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY, routing_mode="fifo")

    with ContentStore(db_path=tmp_path / "test.db") as store:
        for post in schedule:
            store.insert_slot_if_missing(
                post.scheduled_at.isoformat(), post.content_type, post.prompt, "2026-06-01T00:00:00",
            )
        rows = store._conn.execute("SELECT pillar_key FROM content_slots").fetchall()

    assert len(rows) == len(schedule)
    assert all(row["pillar_key"] is None for row in rows)


# ---------------------------------------------------------------------------
# FIFO slot assignment sequencing
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def _seed_fifo_slots(store, n, start="2026-09-01T09:00:00"):
    from datetime import timedelta
    base = datetime.fromisoformat(start)
    for i in range(n):
        store.insert_slot_if_missing((base + timedelta(days=i)).isoformat(), None, None, "2026-01-01T00:00:00")


def test_fifo_assignment_is_strict_ingestion_order(store):
    """video A ingested first, B second, C third -> A gets slot1, B gets
    slot2, C gets slot3 — no pillar or classifier involved anywhere here."""
    _seed_fifo_slots(store, 3)

    video_a = store.insert_video("hash-a", "a.mp4", "/incoming/a.mp4", "2026-01-01T00:00:01")
    video_b = store.insert_video("hash-b", "b.mp4", "/incoming/b.mp4", "2026-01-01T00:00:02")
    video_c = store.insert_video("hash-c", "c.mp4", "/incoming/c.mp4", "2026-01-01T00:00:03")

    import slot_matcher

    assignments = []
    for video in (video_a, video_b, video_c):
        slot = slot_matcher.select_slot_fifo(store, now=datetime(2000, 1, 1))
        store.assign_slot(video.id, slot.id)
        assignments.append((video.file_hash, slot.scheduled_at))

    assert assignments == [
        ("hash-a", "2026-09-01T09:00:00"),
        ("hash-b", "2026-09-02T09:00:00"),
        ("hash-c", "2026-09-03T09:00:00"),
    ]


def test_fifo_matching_ignores_pillar_entirely(store):
    """A mix of typed (pillar-mode leftover) and untyped slots: FIFO
    matching must pick the earliest OPEN slot regardless of pillar_key."""
    store.insert_slot_if_missing("2026-09-05T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    store.insert_slot_if_missing("2026-09-01T09:00:00", None, None, "2026-01-01T00:00:00")

    import slot_matcher

    slot = slot_matcher.select_slot_fifo(store, now=datetime(2000, 1, 1))

    assert slot.scheduled_at == "2026-09-01T09:00:00"
    assert slot.pillar_key is None


def test_fifo_matching_returns_none_when_no_open_slot(store):
    import slot_matcher

    assert slot_matcher.select_slot_fifo(store, now=datetime(2000, 1, 1)) is None
