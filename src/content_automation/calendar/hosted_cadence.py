"""
hosted_cadence.py — per-user hosted posting-cadence slot generation
(Milestone 3.8: Scheduling + Cadence Configuration).

What it does:
  The hosted counterpart to calendar/cadence.py's WHEN half
  (generate_posting_dates), but a genuinely different shape: a rolling
  day-based horizon (not one calendar month), an explicit per-user IANA
  timezone (not one global config.TIMEZONE), and an arbitrary number of
  posting times per active weekday (not exactly one global POSTING_TIME).
  cadence.py itself is left untouched — it still serves the unrelated
  CLI/global path unchanged; see
  docs/decisions/0012-hosted-cadence-configuration.md for why this earned
  its own module instead of overloading that one.

  generate_slot_datetimes() is pure (stdlib only, no I/O) — it takes a
  cadence's posting_times/timezone/now and returns the real calendar
  instants those imply over a bounded horizon. The persistence side
  (api/routes/cadence.py) converts its output to (naive-local-string,
  timezone) pairs and hands them to
  persistence.protocol.ContentStoreProtocol.save_cadence_and_regenerate_slots
  in one atomic call — this module never touches a ContentStore itself,
  matching cadence.py's own "pure math, caller does I/O" shape.

DST policy (explicit, deliberate, tested — see tests/test_hosted_cadence.py):
  - A nonexistent local time (spring-forward gap, e.g. 2:30 AM on the date
    a zone jumps 2:00->3:00) is detected by round-tripping the candidate
    through UTC and back, and comparing wall-clock components to the
    original — if they don't match, that instance is skipped entirely for
    that day, never silently shifted to some other hour.
  - An ambiguous local time (fall-back, e.g. 1:30 AM occurring twice) is
    resolved via Python's default fold=0 (the earlier of the two real
    instants) — an explicit choice, not an accidental default. This
    doesn't change what gets stored (the naive-local string is identical
    either way); it only matters if a future consumer ever converts
    scheduled_at+timezone into a real instant.

Dependencies:
  stdlib only (datetime, zoneinfo). calendar.cadence (WEEKDAY_NAMES,
  parse_posting_time, ScheduleConfigError — reused, not redefined).
"""

from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from content_automation.calendar.cadence import WEEKDAY_NAMES, ScheduleConfigError, parse_posting_time


def validate_timezone(tz: str) -> None:
    """Raise ScheduleConfigError for a timezone string zoneinfo doesn't
    recognize — checked eagerly (not left to fail deep inside generation)
    so a bad value is rejected at save time with a clear message."""
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleConfigError(f"Unknown timezone {tz!r} — expected an IANA name like 'America/New_York'.") from exc


def validate_posting_times(posting_times: list[tuple[str, str]]) -> list[tuple[str, time]]:
    """Validate and parse a cadence's raw (weekday, 'HH:MM') pairs.
    Returns the same pairs with posting_time parsed into a real time
    object. Raises ScheduleConfigError for an unknown weekday name or an
    unparseable time — reuses cadence.parse_posting_time's HH:MM parsing
    rather than reimplementing it, but re-raises with a message naming
    the actual offending pair (parse_posting_time's own message is
    written for the single global POSTING_TIME setting, not a list)."""
    parsed = []
    for weekday, raw_time in posting_times:
        if weekday not in WEEKDAY_NAMES:
            raise ScheduleConfigError(
                f"Unknown weekday {weekday!r} — expected one of {WEEKDAY_NAMES}."
            )
        try:
            parsed_time = parse_posting_time(raw_time)
        except ScheduleConfigError as exc:
            raise ScheduleConfigError(f"Invalid posting time {raw_time!r} for {weekday}: {exc}") from exc
        parsed.append((weekday, parsed_time))
    return parsed


def _is_real_local_time(candidate: datetime) -> bool:
    """True if candidate (aware) round-trips through UTC and back to the
    same wall-clock components — False means this naive local time never
    actually existed (a spring-forward DST gap): zoneinfo will still
    happily construct *some* aware datetime for it (it never raises on
    its own), extrapolating from the offset in effect just before or
    after the gap, which is exactly the silently-wrong behavior this
    check exists to catch and reject."""
    roundtrip = candidate.astimezone(dt_timezone.utc).astimezone(candidate.tzinfo)
    return (
        (roundtrip.year, roundtrip.month, roundtrip.day, roundtrip.hour, roundtrip.minute)
        == (candidate.year, candidate.month, candidate.day, candidate.hour, candidate.minute)
    )


def generate_slot_datetimes(
    posting_times: list[tuple[str, time]],
    tz: str,
    now: datetime,
    horizon_days: int,
) -> list[datetime]:
    """For each day in [today, today + horizon_days) in `tz`, emit one
    aware datetime per configured (weekday, time) pair whose weekday
    matches that day — skipping anything at or before `now`, and skipping
    (see module docstring) any candidate that names a local time which
    does not exist that day. Deterministic, sorted ascending, no DB
    access.

    `now` may be naive or aware; either way it is interpreted in `tz` for
    determining "today" and the not-yet-past boundary — a caller
    comparing against a different timezone's idea of "now" is a caller
    bug, not something this function can detect.

    `posting_times` takes already-parsed (weekday, time) pairs — see
    validate_posting_times — not raw 'HH:MM' strings, so this function
    itself never raises on malformed input; validation is the caller's
    job, done once, before generation.
    """
    zone = ZoneInfo(tz)
    aware_now = now.astimezone(zone) if now.tzinfo is not None else now.replace(tzinfo=zone)

    by_weekday: dict[int, list[time]] = {}
    for weekday, posting_time in posting_times:
        by_weekday.setdefault(WEEKDAY_NAMES.index(weekday), []).append(posting_time)

    generated: list[datetime] = []
    start_day = aware_now.date()
    for offset in range(horizon_days):
        day: date = start_day + timedelta(days=offset)
        times_today = by_weekday.get(day.weekday())
        if not times_today:
            continue
        for posting_time in times_today:
            candidate = datetime.combine(day, posting_time, tzinfo=zone)
            if not _is_real_local_time(candidate):
                continue  # spring-forward gap — this instance never existed
            if candidate <= aware_now:
                continue
            generated.append(candidate)

    generated.sort()
    return generated


def build_generated_slots(
    posting_times: list[tuple[str, str]],
    tz: str,
    is_active: bool,
    now: datetime,
    horizon_days: int,
) -> list[tuple[str, str]]:
    """The one entry point api/routes/cadence.py calls: validates the raw
    input, generates the horizon (or returns an empty list outright for an
    inactive cadence — see ContentStoreProtocol.save_cadence_and_regenerate_slots's
    own docstring for why the reconciliation DELETE still needs to run in
    that case), and converts each aware datetime to the naive-local ISO
    string actually stored, paired with the timezone it was computed in.
    No change to how scheduled_at is interpreted anywhere else in this
    codebase — this only produces values in the exact same shape existing
    rows already have.
    """
    validate_timezone(tz)
    parsed_times = validate_posting_times(posting_times)
    if not is_active:
        return []
    datetimes = generate_slot_datetimes(parsed_times, tz, now, horizon_days)
    return [(dt.replace(tzinfo=None).isoformat(), tz) for dt in datetimes]
