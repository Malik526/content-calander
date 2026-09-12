"""Tests for scheduling.py: posting-date generation, pillar allocation,
pillar sequencing, configuration validation, and future-only filtering."""

from datetime import datetime, time

import pytest

import scheduling
from scheduling import (
    ScheduleConfigError,
    allocate_pillars,
    auto_posting_weekdays,
    distribute_pillars,
    filter_future_dates,
    generate_posting_dates,
    parse_posting_time,
    resolve_posting_weekdays,
    validate_cadence_config,
    validate_pillar_weights,
    validate_routing_mode,
    validate_schedule_config,
)

# ---------------------------------------------------------------------------
# Posting-date generation
# ---------------------------------------------------------------------------

def test_february_non_leap_year():
    dates = generate_posting_dates(2026, 2, 7, "auto", "09:00")
    assert len(dates) == 28
    assert all(d.month == 2 and d.year == 2026 for d in dates)


def test_february_leap_year():
    dates = generate_posting_dates(2024, 2, 7, "auto", "09:00")
    assert len(dates) == 29


def test_30_day_month():
    dates = generate_posting_dates(2026, 9, 7, "auto", "09:00")
    assert len(dates) == 30


def test_31_day_month():
    dates = generate_posting_dates(2026, 10, 7, "auto", "09:00")
    assert len(dates) == 31


def test_explicit_posting_days_generates_only_those_weekdays():
    dates = generate_posting_dates(2026, 9, 3, ["monday", "wednesday", "friday"], "09:00")
    weekday_names = {d.strftime("%A").lower() for d in dates}
    assert weekday_names == {"monday", "wednesday", "friday"}


def test_auto_posting_days_used_when_configured():
    dates = generate_posting_dates(2026, 9, 3, "auto", "09:00")
    weekday_names = {d.strftime("%A").lower() for d in dates}
    assert len(weekday_names) == 3


@pytest.mark.parametrize("posts_per_week", range(1, 8))
def test_one_through_seven_posts_per_week(posts_per_week):
    dates = generate_posting_dates(2026, 9, posts_per_week, "auto", "09:00")
    weekday_names = {d.strftime("%A").lower() for d in dates}
    assert len(weekday_names) == posts_per_week


def test_correct_posting_time_applied():
    dates = generate_posting_dates(2026, 9, 7, "auto", "14:30")
    assert all(d.hour == 14 and d.minute == 30 for d in dates)


def test_dates_are_ascending():
    dates = generate_posting_dates(2026, 9, 7, "auto", "09:00")
    assert dates == sorted(dates)


def test_no_dates_outside_target_month():
    dates = generate_posting_dates(2026, 9, 7, "auto", "09:00")
    assert all(d.month == 9 for d in dates)


def test_total_slots_derived_from_real_calendar_not_posts_per_week_times_four():
    """September 2026 has 4 Mondays, 5 Wednesdays, 4 Fridays -> 13, not 3*4=12."""
    dates = generate_posting_dates(2026, 9, 3, ["monday", "wednesday", "friday"], "09:00")
    assert len(dates) == 13


# ---------------------------------------------------------------------------
# Auto posting-day distribution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", range(1, 8))
def test_auto_posting_weekdays_returns_n_distinct_valid_names(n):
    days = auto_posting_weekdays(n)
    assert len(days) == n
    assert len(set(days)) == n
    assert all(day in scheduling.WEEKDAY_NAMES for day in days)


def test_auto_posting_weekdays_is_deterministic():
    assert auto_posting_weekdays(3) == auto_posting_weekdays(3)


def test_auto_posting_weekdays_seven_is_every_day():
    assert set(auto_posting_weekdays(7)) == set(scheduling.WEEKDAY_NAMES)


# ---------------------------------------------------------------------------
# Pillar allocation (largest remainder)
# ---------------------------------------------------------------------------

def test_allocation_worked_example_from_spec():
    counts = allocate_pillars(13, {"building": 0.50, "acquisition": 0.30, "mindset": 0.20})
    assert counts == {"building": 6, "acquisition": 4, "mindset": 3}
    assert sum(counts.values()) == 13


def test_allocation_one_pillar():
    counts = allocate_pillars(10, {"only": 1.0})
    assert counts == {"only": 10}


def test_allocation_two_pillars():
    counts = allocate_pillars(7, {"a": 0.5, "b": 0.5})
    assert sum(counts.values()) == 7


def test_allocation_four_pillars_matches_original_calendar_weights():
    counts = allocate_pillars(30, {
        "acquisition": 0.40, "building": 0.25, "execution": 0.20, "mindset": 0.15,
    })
    assert counts == {"acquisition": 12, "building": 8, "execution": 6, "mindset": 4}
    assert sum(counts.values()) == 30


def test_allocation_arbitrary_n_pillars():
    weights = {f"pillar_{i}": 1 / 6 for i in range(6)}
    counts = allocate_pillars(20, weights)
    assert sum(counts.values()) == 20
    assert len(counts) == 6


def test_allocation_exact_when_evenly_divisible():
    counts = allocate_pillars(8, {"a": 0.5, "b": 0.5})
    assert counts == {"a": 4, "b": 4}


def test_allocation_tied_remainder_breaks_by_config_order():
    # Both pillars land on exactly .5 remainder for an odd total; "a" is
    # declared first so it wins the tie for the remaining slot.
    counts = allocate_pillars(5, {"a": 0.5, "b": 0.5})
    assert counts["a"] == 3
    assert counts["b"] == 2


def test_allocation_total_equals_input_across_range():
    weights = {"a": 0.33, "b": 0.33, "c": 0.34}
    for total in range(0, 25):
        counts = allocate_pillars(total, weights)
        assert sum(counts.values()) == total


def test_allocation_rejects_invalid_weights():
    with pytest.raises(ScheduleConfigError):
        allocate_pillars(10, {"a": 0.5, "b": 0.6})


# ---------------------------------------------------------------------------
# Pillar weight validation
# ---------------------------------------------------------------------------

def test_validate_pillar_weights_rejects_empty():
    with pytest.raises(ScheduleConfigError):
        validate_pillar_weights({})


def test_validate_pillar_weights_rejects_negative():
    with pytest.raises(ScheduleConfigError):
        validate_pillar_weights({"a": -0.1, "b": 1.1})


def test_validate_pillar_weights_rejects_sum_not_one():
    with pytest.raises(ScheduleConfigError):
        validate_pillar_weights({"a": 0.5, "b": 0.4})


def test_validate_pillar_weights_accepts_floating_point_tolerance():
    validate_pillar_weights({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})  # does not raise


# ---------------------------------------------------------------------------
# Pillar sequencing (distribution across dates)
# ---------------------------------------------------------------------------

def test_distribute_pillars_preserves_exact_counts():
    counts = {"building": 6, "acquisition": 4, "mindset": 3}
    sequence = distribute_pillars(counts)
    assert len(sequence) == 13
    assert {k: sequence.count(k) for k in counts} == counts


def test_distribute_pillars_is_deterministic():
    counts = {"building": 6, "acquisition": 4, "mindset": 3}
    assert distribute_pillars(counts) == distribute_pillars(counts)


def test_distribute_pillars_does_not_cluster():
    """The naive 'all of A then all of B' output should not appear when more
    than one pillar has more than one slot."""
    counts = {"building": 6, "acquisition": 4, "mindset": 3}
    sequence = distribute_pillars(counts)
    max_run = 1
    run = 1
    for prev, cur in zip(sequence, sequence[1:]):
        run = run + 1 if cur == prev else 1
        max_run = max(max_run, run)
    assert max_run <= 3  # far short of a 6-in-a-row cluster


def test_distribute_pillars_single_pillar():
    assert distribute_pillars({"only": 5}) == ["only"] * 5


def test_distribute_pillars_empty_when_all_zero():
    assert distribute_pillars({"a": 0, "b": 0}) == []


# ---------------------------------------------------------------------------
# Posting time parsing
# ---------------------------------------------------------------------------

def test_parse_posting_time_accepts_hh_mm():
    assert parse_posting_time("14:30") == time(14, 30)


def test_parse_posting_time_rejects_invalid_format():
    with pytest.raises(ScheduleConfigError):
        parse_posting_time("not-a-time")


def test_parse_posting_time_rejects_out_of_range():
    with pytest.raises(ScheduleConfigError):
        parse_posting_time("25:00")


# ---------------------------------------------------------------------------
# Config validation (section 17 cases)
# ---------------------------------------------------------------------------

def test_rejects_posts_per_week_zero():
    with pytest.raises(ScheduleConfigError):
        resolve_posting_weekdays(0, "auto")


def test_rejects_posts_per_week_eight():
    with pytest.raises(ScheduleConfigError):
        resolve_posting_weekdays(8, "auto")


def test_rejects_duplicate_posting_days():
    with pytest.raises(ScheduleConfigError):
        resolve_posting_weekdays(2, ["monday", "monday"])


def test_rejects_explicit_day_count_mismatch():
    with pytest.raises(ScheduleConfigError):
        resolve_posting_weekdays(3, ["monday", "wednesday"])


def test_rejects_unknown_weekday_name():
    with pytest.raises(ScheduleConfigError):
        resolve_posting_weekdays(1, ["funday"])


def test_validate_schedule_config_end_to_end_valid():
    validate_schedule_config(3, ["monday", "wednesday", "friday"], "10:00", {"a": 0.5, "b": 0.5})


def test_validate_schedule_config_surfaces_weight_errors():
    with pytest.raises(ScheduleConfigError):
        validate_schedule_config(3, "auto", "10:00", {"a": 0.5, "b": 0.6})


# ---------------------------------------------------------------------------
# Future-only filtering (Milestone 1.1.1)
# ---------------------------------------------------------------------------

def test_filter_future_dates_removes_past_dates_midway_through_month():
    dates = generate_posting_dates(2026, 9, 7, "auto", "09:00")
    start_at = datetime(2026, 9, 12, 13, 0)  # Sep 12, 1:00 PM

    future = filter_future_dates(dates, start_at)

    assert all(dt >= start_at for dt in future)
    assert len(future) == len([dt for dt in dates if dt.date() >= start_at.date() and dt >= start_at])
    assert len(future) == 18  # Sep 13-30 inclusive, one per day at 7 posts/week


def test_filter_future_dates_keeps_same_day_slot_when_posting_time_still_ahead():
    dates = [datetime(2026, 9, 12, 9, 0)]
    start_at = datetime(2026, 9, 12, 8, 0)  # now is before today's 9:00 AM slot

    assert filter_future_dates(dates, start_at) == [datetime(2026, 9, 12, 9, 0)]


def test_filter_future_dates_excludes_same_day_slot_when_posting_time_already_passed():
    dates = [datetime(2026, 9, 12, 9, 0)]
    start_at = datetime(2026, 9, 12, 10, 0)  # now is after today's 9:00 AM slot

    assert filter_future_dates(dates, start_at) == []


def test_filter_future_dates_boundary_is_inclusive():
    """scheduled_at == start_at must be kept (>=, not >)."""
    boundary = datetime(2026, 9, 12, 9, 0)
    dates = [boundary]

    assert filter_future_dates(dates, boundary) == [boundary]


def test_filter_future_dates_future_month_is_unchanged():
    dates = generate_posting_dates(2026, 10, 7, "auto", "09:00")
    start_at = datetime(2026, 9, 12, 13, 0)  # "now" is entirely before October

    assert filter_future_dates(dates, start_at) == dates


def test_filter_future_dates_entirely_past_month_returns_empty():
    dates = generate_posting_dates(2026, 8, 7, "auto", "09:00")
    start_at = datetime(2026, 9, 12, 13, 0)  # "now" is entirely after August

    assert filter_future_dates(dates, start_at) == []


def test_filter_future_dates_preserves_ascending_order():
    dates = generate_posting_dates(2026, 9, 7, "auto", "09:00")
    start_at = datetime(2026, 9, 5, 0, 0)

    future = filter_future_dates(dates, start_at)

    assert future == sorted(future)


# ---------------------------------------------------------------------------
# Routing mode (Milestone 1.3)
# ---------------------------------------------------------------------------

def test_validate_routing_mode_accepts_fifo_and_pillar():
    validate_routing_mode("fifo")  # must not raise
    validate_routing_mode("pillar")  # must not raise


def test_validate_routing_mode_rejects_unknown_value():
    with pytest.raises(ScheduleConfigError, match="Unsupported ROUTING_MODE='bogus'"):
        validate_routing_mode("bogus")


def test_validate_cadence_config_ignores_pillar_weights():
    """FIFO mode needs only WHEN (cadence) validated, not WHAT (pillar
    weights) — validate_cadence_config takes no pillar_weights argument at
    all, so an invalid/empty pillar configuration can never block it."""
    validate_cadence_config(3, "auto", "09:00")  # must not raise


def test_validate_cadence_config_rejects_invalid_cadence():
    with pytest.raises(ScheduleConfigError):
        validate_cadence_config(9, "auto", "09:00")


def test_validate_schedule_config_still_validates_both_cadence_and_weights():
    """Pillar mode keeps the original combined validation."""
    validate_schedule_config(3, "auto", "09:00", {"a": 1.0})  # must not raise
    with pytest.raises(ScheduleConfigError):
        validate_schedule_config(3, "auto", "09:00", {"a": 0.5})
