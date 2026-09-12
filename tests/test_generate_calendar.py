"""Tests for generate_calendar.py's composition of scheduling.py, and its
content_slots persistence hook (idempotency + the new scheduled_at-only
uniqueness invariant).

These pass an explicit, fixed-in-the-past start_at so full-month composition
stays deterministic regardless of wall-clock time — the future-only
filtering behavior itself (start_at defaulted, partial/entirely-past months)
is covered separately in tests/test_future_only_schedule.py."""

from datetime import datetime

from content_store import ContentStore
from generate_calendar import build_event_body, build_schedule, get_content_label

PAST_BOUNDARY = datetime(2000, 1, 1)  # old enough that no configured month is ever "in the past" relative to it


def test_build_schedule_matches_configured_pillar_weights():
    schedule = build_schedule(2026, 9, start_at=PAST_BOUNDARY)  # September 2026: default POSTS_PER_WEEK, all pillars
    counts = {}
    for post in schedule:
        counts[post.content_type] = counts.get(post.content_type, 0) + 1

    assert sum(counts.values()) == len(schedule)
    assert set(counts.keys()) <= {"engineering", "career", "building_in_public", "mindset"}


def test_build_schedule_is_deterministic():
    first = build_schedule(2026, 6, start_at=PAST_BOUNDARY)
    second = build_schedule(2026, 6, start_at=PAST_BOUNDARY)

    assert [(p.scheduled_at, p.content_type, p.prompt) for p in first] == [
        (p.scheduled_at, p.content_type, p.prompt) for p in second
    ]


def test_build_schedule_attaches_prompts_by_default():
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY)
    assert all(post.prompt for post in schedule)


def test_build_event_body_uses_scheduled_at_directly():
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY)
    post = schedule[0]

    body = build_event_body(post)

    assert body["start"]["dateTime"] == post.scheduled_at.isoformat()
    assert body["summary"] == f"POST — {get_content_label(post.content_type)}"


def test_generate_calendar_persists_content_slots_idempotently(tmp_path):
    """Re-running generation for the same month must not duplicate content_slots."""
    schedule = build_schedule(2026, 6, start_at=PAST_BOUNDARY)

    with ContentStore(db_path=tmp_path / "test.db") as store:
        created_first_run = sum(
            1 for post in schedule
            if store.insert_slot_if_missing(
                post.scheduled_at.isoformat(), post.content_type, post.prompt, "2026-06-01T00:00:00",
            )
        )
        created_second_run = sum(
            1 for post in schedule
            if store.insert_slot_if_missing(
                post.scheduled_at.isoformat(), post.content_type, post.prompt, "2026-06-02T00:00:00",
            )
        )

        assert created_first_run == len(schedule)
        assert created_second_run == 0


def test_content_slots_unique_on_scheduled_at_even_across_different_pillars(tmp_path):
    """A changed strategy must never create two slots for the same posting
    datetime, even if it would now assign a different pillar to it."""
    with ContentStore(db_path=tmp_path / "test.db") as store:
        created_first = store.insert_slot_if_missing(
            "2026-09-14T09:00:00", "building", "prompt A", "2026-01-01T00:00:00",
        )
        created_second = store.insert_slot_if_missing(
            "2026-09-14T09:00:00", "acquisition", "prompt B (different pillar)", "2026-01-02T00:00:00",
        )

        assert created_first is True
        assert created_second is False  # existing slot's pillar is never silently overwritten

        slot = store.find_earliest_open_slot("building", "2000-01-01T00:00:00")
        assert slot is not None
        assert slot.pillar_key == "building"  # original assignment preserved
