"""Integration tests for Milestone 1.1.1 (future-only calendar generation):
build_schedule() reallocates pillar weights against only the remaining
future slots, main() handles an entirely-past month cleanly with no
Calendar/DB writes, and content_slots never gains a past-dated row.
All Google API calls mocked; no live credentials required.
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import calendar_manager
import generate_calendar
from config import CONTENT_TYPES, POSTING_DAYS, POSTING_TIME, POSTS_PER_WEEK
from content_store import ContentStore
from generate_calendar import build_schedule
from scheduling import allocate_pillars, generate_posting_dates


def test_build_schedule_default_start_at_uses_now_in_config_timezone(monkeypatch):
    """start_at omitted -> resolves via slot_matcher.now_in_config_timezone,
    not a second timezone convention."""
    monkeypatch.setattr(generate_calendar, "now_in_config_timezone", lambda: datetime(2026, 9, 12, 13, 0))

    schedule = build_schedule(2026, 9)

    assert all(post.scheduled_at >= datetime(2026, 9, 12, 13, 0) for post in schedule)


def test_build_schedule_reallocates_against_remaining_future_count():
    """Trimming a month to N remaining dates must reallocate pillar counts
    to sum to N — not the original full-month count."""
    start_at = datetime(2026, 9, 12, 13, 0)

    full_dates = generate_posting_dates(2026, 9, POSTS_PER_WEEK, POSTING_DAYS, POSTING_TIME)
    future_dates = [dt for dt in full_dates if dt >= start_at]
    assert len(future_dates) < len(full_dates)  # sanity: this month really is partly elapsed here

    schedule = build_schedule(2026, 9, start_at=start_at)

    assert len(schedule) == len(future_dates)
    pillar_weights = {key: info["weight"] for key, info in CONTENT_TYPES.items()}
    expected_counts = allocate_pillars(len(future_dates), pillar_weights)
    actual_counts = {}
    for post in schedule:
        actual_counts[post.content_type] = actual_counts.get(post.content_type, 0) + 1
    for key in pillar_weights:
        assert actual_counts.get(key, 0) == expected_counts[key]


def test_build_schedule_future_month_is_unaffected():
    """A month entirely after start_at generates its full schedule, exactly
    as it did before this feature."""
    start_at = datetime(2026, 9, 1, 0, 0)

    with_boundary = build_schedule(2026, 10, start_at=start_at)
    without_boundary = build_schedule(2026, 10, start_at=datetime(2000, 1, 1))

    assert len(with_boundary) == len(without_boundary)


def test_build_schedule_entirely_past_month_returns_empty_without_error():
    start_at = datetime(2026, 10, 1, 0, 0)

    schedule = build_schedule(2026, 9, start_at=start_at)  # September is entirely before October 1

    assert schedule == []


def test_build_schedule_no_dates_earlier_than_start_at():
    start_at = datetime(2026, 9, 12, 8, 30)

    schedule = build_schedule(2026, 9, start_at=start_at)

    assert all(post.scheduled_at >= start_at for post in schedule)


def test_only_future_slots_are_persisted(tmp_path):
    start_at = datetime(2026, 9, 12, 13, 0)
    schedule = build_schedule(2026, 9, start_at=start_at)

    with ContentStore(db_path=tmp_path / "test.db") as store:
        for post in schedule:
            store.insert_slot_if_missing(
                post.scheduled_at.isoformat(), post.content_type, post.prompt, "2026-09-12T13:00:00",
            )

        rows = store._conn.execute("SELECT scheduled_at FROM content_slots").fetchall()

    persisted = [datetime.fromisoformat(row["scheduled_at"]) for row in rows]
    assert all(dt >= start_at for dt in persisted)
    assert len(persisted) == len(schedule)


# ---------------------------------------------------------------------------
# CLI: main() handling of an entirely-past month
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_manager, "APP_CALENDAR_STATE_PATH", tmp_path / "calendar_state.json")


def test_main_reports_no_future_slots_and_touches_nothing_for_past_month(monkeypatch, capsys):
    """Definition of Done #5/#6: entirely past month -> clean message, zero
    Calendar/DB writes, no exception."""
    monkeypatch.setattr(sys, "argv", ["generate_calendar.py", "--month", "9", "--year", "2026"])
    monkeypatch.setattr(generate_calendar, "now_in_config_timezone", lambda: datetime(2026, 10, 1, 0, 0))

    def _fail(*a, **k):
        raise AssertionError("must not be called when there are no future slots")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _fail)
    monkeypatch.setattr(generate_calendar, "build_calendar_service", _fail)
    monkeypatch.setattr(generate_calendar, "ContentStore", _fail)

    generate_calendar.main()  # must not raise

    out = capsys.readouterr().out
    assert "No future posting slots remain for September 2026." in out


def test_main_dry_run_past_month_reports_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["generate_calendar.py", "--month", "9", "--year", "2026", "--dry-run"]
    )
    monkeypatch.setattr(generate_calendar, "now_in_config_timezone", lambda: datetime(2026, 10, 1, 0, 0))

    generate_calendar.main()  # must not raise, must not print a schedule table

    out = capsys.readouterr().out
    assert "No future posting slots remain for September 2026." in out
    assert "Dry run enabled" not in out  # short-circuits before the dry-run summary path


def test_main_dry_run_partial_month_shows_only_future_slots(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["generate_calendar.py", "--month", "9", "--year", "2026", "--dry-run"]
    )
    fixed_now = datetime(2026, 9, 12, 13, 0)
    monkeypatch.setattr(generate_calendar, "now_in_config_timezone", lambda: fixed_now)

    generate_calendar.main()

    out = capsys.readouterr().out
    expected_total = len(build_schedule(2026, 9, start_at=fixed_now))
    assert f"{expected_total:2d} posts" in out
