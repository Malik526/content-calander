"""Tests for calendar.hosted_cadence (Milestone 3.8) — pure functions,
no DB, no network. Covers generation determinism, timezone handling,
multiple-times-per-day, inactive-cadence, horizon bounding, and the two
explicit DST policies (spring-forward gap skipped, fall-back ambiguity
resolved deterministically via fold=0)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from content_automation.calendar.cadence import ScheduleConfigError
from content_automation.calendar.hosted_cadence import (
    build_generated_slots,
    generate_slot_datetimes,
    validate_posting_times,
    validate_timezone,
)


def _weekday_name_for(d: date) -> str:
    from content_automation.calendar.cadence import WEEKDAY_NAMES

    return WEEKDAY_NAMES[d.weekday()]


def test_generates_one_slot_for_a_single_matching_weekday_time_pair():
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York"))  # a Thursday
    thursday = _weekday_name_for(date(2026, 10, 1))
    result = generate_slot_datetimes([(thursday, time(9, 0))], "America/New_York", now, horizon_days=14)

    assert len(result) == 2  # Oct 1 (09:00, after midnight `now`) and Oct 8 -- Oct 15 falls outside the 14-day window
    for dt in result:
        assert dt.hour == 9 and dt.minute == 0
        assert dt.tzinfo is not None


def test_multiple_times_per_day():
    now = datetime(2026, 10, 1, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    monday = _weekday_name_for(date(2026, 10, 5))  # the following Monday
    result = generate_slot_datetimes(
        [(monday, time(9, 0)), (monday, time(18, 0))], "America/New_York", now, horizon_days=7,
    )
    times = sorted((dt.date(), dt.time()) for dt in result)
    assert times == [(date(2026, 10, 5), time(9, 0)), (date(2026, 10, 5), time(18, 0))]


def test_skips_candidates_at_or_before_now():
    now = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/New_York"))  # exactly at 09:00 Thursday
    thursday = _weekday_name_for(date(2026, 10, 1))
    result = generate_slot_datetimes([(thursday, time(9, 0))], "America/New_York", now, horizon_days=1)
    assert result == []  # the only candidate in a 1-day window is exactly `now` -- excluded


def test_horizon_is_bounded():
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York"))
    # every day matches -- one time per weekday, all seven
    from content_automation.calendar.cadence import WEEKDAY_NAMES

    posting_times = [(w, time(9, 0)) for w in WEEKDAY_NAMES]
    result = generate_slot_datetimes(posting_times, "America/New_York", now, horizon_days=28)
    assert len(result) == 28  # exactly one per day, never more than the horizon


def test_deterministic_and_sorted_ascending():
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York"))
    from content_automation.calendar.cadence import WEEKDAY_NAMES

    posting_times = [(w, time(9, 0)) for w in WEEKDAY_NAMES]
    result_a = generate_slot_datetimes(posting_times, "America/New_York", now, horizon_days=10)
    result_b = generate_slot_datetimes(posting_times, "America/New_York", now, horizon_days=10)
    assert result_a == result_b
    assert result_a == sorted(result_a)


def test_respects_the_cadences_own_timezone_not_server_local():
    """The same wall-clock spec in two different timezones must produce
    two different real UTC instants -- proving generation actually uses
    the cadence's own timezone, not a shared/global one."""
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("UTC"))
    thursday = _weekday_name_for(date(2026, 10, 1))
    eastern = generate_slot_datetimes([(thursday, time(9, 0))], "America/New_York", now, horizon_days=8)
    pacific = generate_slot_datetimes([(thursday, time(9, 0))], "America/Los_Angeles", now, horizon_days=8)
    assert eastern[0].astimezone(ZoneInfo("UTC")) != pacific[0].astimezone(ZoneInfo("UTC"))


def test_dst_spring_forward_gap_is_skipped_not_shifted():
    """2027-03-14 is when America/New_York jumps 2:00 AM -> 3:00 AM.
    2:30 AM that day never existed as a real local time -- it must be
    skipped entirely, not silently reinterpreted as 1:30 or 3:30."""
    gap_day = date(2027, 3, 14)
    weekday = _weekday_name_for(gap_day)
    now = datetime(2027, 3, 10, tzinfo=ZoneInfo("America/New_York"))  # a few days before

    result = generate_slot_datetimes([(weekday, time(2, 30))], "America/New_York", now, horizon_days=14)

    assert all(dt.date() != gap_day for dt in result)  # the gap day generated nothing
    # a neighboring week's same weekday+time IS generated -- proves this
    # is specific to the gap day, not a blanket failure of 2:30 AM slots
    assert any(dt.date() == gap_day + __import__("datetime").timedelta(days=7) for dt in result)


def test_dst_fall_back_ambiguous_time_is_still_generated():
    """2026-11-01 is when America/New_York falls back 2:00 AM -> 1:00 AM.
    1:30 AM occurs twice that day; Python's default fold=0 (the earlier,
    still-DST instant) is used deterministically -- the slot must still
    be generated, not skipped."""
    fold_day = date(2026, 11, 1)
    weekday = _weekday_name_for(fold_day)
    now = datetime(2026, 10, 28, tzinfo=ZoneInfo("America/New_York"))

    result = generate_slot_datetimes([(weekday, time(1, 30))], "America/New_York", now, horizon_days=14)

    matches = [dt for dt in result if dt.date() == fold_day]
    assert len(matches) == 1
    assert matches[0].fold == 0


def test_validate_timezone_rejects_unknown_zone():
    with pytest.raises(ScheduleConfigError):
        validate_timezone("Not/A_Real_Zone")


def test_validate_timezone_accepts_real_iana_name():
    validate_timezone("America/New_York")  # must not raise


def test_validate_posting_times_rejects_unknown_weekday():
    with pytest.raises(ScheduleConfigError):
        validate_posting_times([("funday", "09:00")])


def test_validate_posting_times_rejects_malformed_time():
    with pytest.raises(ScheduleConfigError):
        validate_posting_times([("monday", "9am")])


def test_validate_posting_times_parses_valid_input():
    result = validate_posting_times([("monday", "09:00"), ("friday", "18:30")])
    assert result == [("monday", time(9, 0)), ("friday", time(18, 30))]


def test_build_generated_slots_inactive_cadence_returns_nothing():
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York"))
    result = build_generated_slots(
        [("monday", "09:00")], "America/New_York", is_active=False, now=now, horizon_days=28,
    )
    assert result == []


def test_build_generated_slots_returns_naive_local_iso_strings_paired_with_timezone():
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York"))
    thursday = _weekday_name_for(date(2026, 10, 1))
    result = build_generated_slots(
        [(thursday, "09:00")], "America/New_York", is_active=True, now=now, horizon_days=6,
    )
    assert len(result) == 1
    scheduled_at, tz = result[0]
    assert tz == "America/New_York"
    assert "T09:00:00" in scheduled_at
    assert "+" not in scheduled_at and "Z" not in scheduled_at  # naive, not aware
