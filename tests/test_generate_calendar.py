"""Locks existing generate_calendar.py schedule behavior (unchanged by this
pipeline work) and tests the new content_slots persistence hook."""

from config import FIFTH_SUNDAY_CONTENT_TYPE, WEEKLY_SCHEDULE
from content_store import ContentStore
from generate_calendar import build_schedule, get_month_dates, slot_start_datetime


def test_build_schedule_matches_weekly_mapping_except_fifth_sunday():
    dates = get_month_dates(2026, 3)  # March 2026 has five Sundays
    schedule = build_schedule(dates)

    sundays = [post for post in schedule if post.day.weekday() == 6]
    assert len(sundays) == 5
    assert sundays[-1].content_type == FIFTH_SUNDAY_CONTENT_TYPE

    for post in schedule:
        if post is sundays[-1]:
            continue
        weekday_name = post.day.strftime("%A").lower()
        assert post.content_type == WEEKLY_SCHEDULE[weekday_name]


def test_build_schedule_is_deterministic():
    dates = get_month_dates(2026, 6)
    first = build_schedule(dates)
    second = build_schedule(dates)

    assert [(p.day, p.content_type, p.prompt) for p in first] == [
        (p.day, p.content_type, p.prompt) for p in second
    ]


def test_slot_start_datetime_matches_configured_hour():
    dates = get_month_dates(2026, 6)
    post = build_schedule(dates)[0]

    start = slot_start_datetime(post)

    assert start.date() == post.day
    assert start.hour == 9


def test_generate_calendar_persists_content_slots_idempotently(tmp_path):
    """Re-running generation for the same month must not duplicate content_slots
    (the AGENT_POLICY-driven fix requested alongside this milestone)."""
    dates = get_month_dates(2026, 6)
    schedule = build_schedule(dates)

    with ContentStore(db_path=tmp_path / "test.db") as store:
        created_first_run = 0
        for post in schedule:
            if store.insert_slot_if_missing(
                slot_start_datetime(post).isoformat(),
                post.content_type,
                post.prompt,
                "2026-06-01T00:00:00",
            ):
                created_first_run += 1

        created_second_run = 0
        for post in schedule:
            if store.insert_slot_if_missing(
                slot_start_datetime(post).isoformat(),
                post.content_type,
                post.prompt,
                "2026-06-02T00:00:00",
            ):
                created_second_run += 1

        assert created_first_run == len(schedule)
        assert created_second_run == 0
