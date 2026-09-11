"""
scheduling.py — Posting-date generation and pillar allocation.

What it does:
  Answers two separate questions that generate_calendar.py used to couple
  into one fixed weekday->pillar table:

    1. generate_posting_dates(): WHEN does this creator post this month,
       given a posts-per-week cadence, a posting-day strategy (auto or
       explicit), and a posting time?
    2. allocate_pillars() + distribute_pillars(): WHAT pillar does each of
       those posting dates represent, given per-pillar percentage weights?

  Neither function knows about Google Calendar, content_slots, or prompts.
  generate_calendar.py composes them. See
  docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md.

Dependencies:
  stdlib only (calendar, datetime, math).
"""

import calendar
import math
from datetime import date, datetime, time

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ScheduleConfigError(ValueError):
    """Raised for invalid posting-cadence, posting-day, posting-time, or pillar-weight configuration."""


# ---------------------------------------------------------------------------
# WHEN: posting dates
# ---------------------------------------------------------------------------

def get_month_dates(year: int, month: int) -> list[date]:
    """Every date in the given month, ascending."""
    _, days_in_month = calendar.monthrange(year, month)
    return [date(year, month, day) for day in range(1, days_in_month + 1)]


def auto_posting_weekdays(posts_per_week: int) -> list[str]:
    """Deterministically choose `posts_per_week` weekdays, evenly spaced
    across the 7-day week, with no randomization and no LLM.

    Algorithm: for i in 0..posts_per_week-1, the i-th weekday index is
    round(i * 7 / posts_per_week) mod 7 (Monday=0 .. Sunday=6). This is the
    same "evenly spaced indices" approach used to distribute N items across
    a fixed-size cycle (e.g. Bresenham-style line rasterization); it is
    stable across runs since it depends only on posts_per_week.

    posts_per_week is constrained to 1..7 by resolve_posting_weekdays before
    this is called, which guarantees the derived indices are always
    distinct (verified by the length check below as a safety net, not a
    behavior any valid input should ever trigger).
    """
    indices = sorted({round(i * 7 / posts_per_week) % 7 for i in range(posts_per_week)})
    if len(indices) != posts_per_week:
        raise ScheduleConfigError(f"Could not derive {posts_per_week} distinct evenly-spaced weekdays")
    return [WEEKDAY_NAMES[i] for i in indices]


def resolve_posting_weekdays(posts_per_week: int, posting_days) -> list[str]:
    """Validate and resolve POSTS_PER_WEEK/POSTING_DAYS into a list of weekday names.

    posting_days: "auto" to use auto_posting_weekdays(), or an explicit list
    of exactly posts_per_week distinct weekday names. Explicit days always
    override auto distribution.
    """
    if not isinstance(posts_per_week, int) or not (1 <= posts_per_week <= 7):
        raise ScheduleConfigError(f"POSTS_PER_WEEK must be an integer between 1 and 7 (got {posts_per_week!r})")

    if posting_days == "auto":
        return auto_posting_weekdays(posts_per_week)

    if not isinstance(posting_days, (list, tuple)):
        raise ScheduleConfigError("POSTING_DAYS must be 'auto' or a list of weekday names")

    normalized = [str(day).strip().lower() for day in posting_days]
    unknown = sorted(set(day for day in normalized if day not in WEEKDAY_NAMES))
    if unknown:
        raise ScheduleConfigError(f"Unknown weekday name(s) in POSTING_DAYS: {unknown}")
    if len(set(normalized)) != len(normalized):
        raise ScheduleConfigError("POSTING_DAYS contains duplicate weekday names")
    if len(normalized) != posts_per_week:
        raise ScheduleConfigError(
            f"POSTING_DAYS has {len(normalized)} day(s) but POSTS_PER_WEEK is {posts_per_week}"
        )
    return normalized


def parse_posting_time(raw) -> time:
    """Parse 'HH:MM' 24-hour local time. Accepts an already-parsed time unchanged."""
    if isinstance(raw, time):
        return raw
    try:
        hour_str, minute_str = str(raw).split(":")
        return time(hour=int(hour_str), minute=int(minute_str))
    except (ValueError, AttributeError) as exc:
        raise ScheduleConfigError(f"POSTING_TIME must be 'HH:MM' 24-hour format (got {raw!r})") from exc


def generate_posting_dates(
    year: int,
    month: int,
    posts_per_week: int,
    posting_days,
    posting_time,
) -> list[datetime]:
    """Every posting datetime in the target month for the given cadence/day/time config.

    Ascending, restricted to the requested month, one entry per matching
    weekday actually present in that month (not an assumed posts_per_week *
    4 — months don't contain exactly four weeks). Does not assign pillars.
    """
    weekdays = resolve_posting_weekdays(posts_per_week, posting_days)
    resolved_time = parse_posting_time(posting_time)
    weekday_indices = {WEEKDAY_NAMES.index(name) for name in weekdays}

    return [
        datetime.combine(day, resolved_time)
        for day in get_month_dates(year, month)
        if day.weekday() in weekday_indices
    ]


# ---------------------------------------------------------------------------
# WHAT: pillar allocation and distribution
# ---------------------------------------------------------------------------

def validate_pillar_weights(pillar_weights: dict[str, float]) -> None:
    if not pillar_weights:
        raise ScheduleConfigError("CONTENT_TYPES must not be empty")
    total = 0.0
    for key, weight in pillar_weights.items():
        if weight is None:
            raise ScheduleConfigError(f"CONTENT_TYPES['{key}'] is missing a weight")
        if weight < 0:
            raise ScheduleConfigError(f"CONTENT_TYPES['{key}'] has a negative weight: {weight}")
        total += weight
    if abs(total - 1.0) > 1e-6:
        raise ScheduleConfigError(f"CONTENT_TYPES weights must sum to 1.0 (got {total})")


def allocate_pillars(total_slots: int, pillar_weights: dict[str, float]) -> dict[str, int]:
    """Largest-remainder allocation of total_slots across pillar_weights.

    Guarantees: counts sum exactly to total_slots, each count approximates
    total_slots * weight as closely as integer rounding allows, and the
    result is deterministic. Ties in the fractional remainder are broken by
    pillar_weights' iteration (config) order — the earlier-declared pillar
    wins the tied remaining slot.
    """
    validate_pillar_weights(pillar_weights)
    keys = list(pillar_weights.keys())
    if total_slots == 0:
        return {key: 0 for key in keys}
    if total_slots < 0:
        raise ScheduleConfigError(f"total_slots must be >= 0 (got {total_slots})")

    exact = {key: total_slots * pillar_weights[key] for key in keys}
    floors = {key: math.floor(exact[key]) for key in keys}
    remainders = {key: exact[key] - floors[key] for key in keys}

    remaining = total_slots - sum(floors.values())
    ranked_by_remainder = sorted(keys, key=lambda k: (-remainders[k], keys.index(k)))

    counts = dict(floors)
    for key in ranked_by_remainder[:remaining]:
        counts[key] += 1
    return {key: int(counts[key]) for key in keys}


def distribute_pillars(pillar_counts: dict[str, int]) -> list[str]:
    """Order pillar keys across `sum(pillar_counts.values())` slots so each
    pillar is spread evenly rather than clustered, while preserving exact
    counts.

    Uses smooth weighted round-robin (the algorithm nginx uses for weighted
    load balancing): each pillar accumulates "credit" equal to its count
    every round; the pillar with the most credit is chosen and then debited
    by the total. This is deterministic, uses no randomization, and — unlike
    naive "N of A then N of B" — spaces repeated pillars out proportionally
    to their share of the total.

    Ties are broken by pillar_counts' iteration (config) order, since
    max() returns the first-encountered maximum.
    """
    keys = [key for key in pillar_counts if pillar_counts[key] > 0]
    total = sum(pillar_counts[key] for key in keys)
    if total == 0:
        return []

    credit = {key: 0 for key in keys}
    sequence = []
    for _ in range(total):
        for key in keys:
            credit[key] += pillar_counts[key]
        chosen = max(keys, key=lambda k: credit[k])
        sequence.append(chosen)
        credit[chosen] -= total
    return sequence


def validate_schedule_config(posts_per_week, posting_days, posting_time, pillar_weights: dict[str, float]) -> None:
    """Raise ScheduleConfigError with a clear message for any invalid
    posting-cadence/day/time/weight configuration. Called once, before any
    schedule is generated or persisted."""
    resolve_posting_weekdays(posts_per_week, posting_days)
    parse_posting_time(posting_time)
    validate_pillar_weights(pillar_weights)
