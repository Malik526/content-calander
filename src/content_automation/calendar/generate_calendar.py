"""
generate_calendar.py — Content Automation posting-schedule generator.

What it does:
  Generates a month of content calendar events from a configurable posting
  cadence (posts/week, posting-day strategy, posting time) and per-pillar
  percentage weights, and pushes each event to a dedicated, app-owned
  Google Calendar (created/reused via calendar_manager.py, OAuth — never
  "primary"). Generation is future-only: posting datetimes already in the
  past (relative to now, or an injected start_at) are discarded before
  pillar weights are allocated, so a month already partly elapsed only
  gets slots for what's left — see build_schedule(). Prompts are rotated
  sequentially per content type so no prompt repeats until all in its list
  have been used. See
  docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md
  and docs/decisions/0004-dedicated-google-calendar-ownership.md.

Run command (Milestone 3.0: thin CLI entry point at cli/generate_calendar.py):
  python3 cli/generate_calendar.py --month 06 --year 2026
  python3 cli/generate_calendar.py --month 06 --year 2026 --dry-run

  Advanced/debug override — targets an explicit calendar via the shared
  service account instead of the dedicated app calendar; never the default:
  python3 cli/generate_calendar.py --month 06 --year 2026 --calendar <calendar_id>

Dependencies:
  google-auth, google-auth-oauthlib, google-api-python-client  (see requirements.txt)
  content_automation.config, content_automation.calendar.prompts,
  content_automation.calendar.cadence,
  content_automation.persistence.content_store,
  content_automation.calendar.calendar_manager
"""

import calendar
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from content_automation.calendar import calendar_manager
from content_automation.calendar.cadence import allocate_pillars, distribute_pillars, filter_future_dates, generate_posting_dates
from content_automation.calendar.prompts import PROMPTS
from content_automation.config import (
    CONTENT_TYPES,
    EVENT_DURATION_MINUTES,
    POSTING_DAYS,
    POSTING_TIME,
    POSTS_PER_WEEK,
    PROMPT_GENERATION_ENABLED,
    ROUTING_MODE,
    SCOPES,
    SERVICE_ACCOUNT_FILE,
    TIMEZONE,
)
from content_automation.persistence.content_store import ContentStore
from content_automation.scheduling.slot_matcher import now_in_config_timezone

# Event title/description for an untyped FIFO slot — no pillar label exists
# yet to build one from. See
# docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md.
FIFO_EVENT_SUMMARY = "Content Post"


# ---------------------------------------------------------------------------
# Data structure for a single scheduled post
# ---------------------------------------------------------------------------

class ScheduledPost(NamedTuple):
    """One posting slot: the datetime, an optional content pillar key (None
    in FIFO mode — a posting opportunity doesn't inherently have a content
    category), and an optional prompt (always None in FIFO mode)."""
    scheduled_at: datetime
    content_type: str | None
    prompt: str | None


# ---------------------------------------------------------------------------
# Google Calendar service
# ---------------------------------------------------------------------------

def build_calendar_service(service_account_file: str):
    """
    Authenticate with the service account JSON key and return a Calendar API client.

    Raises FileNotFoundError with a clear message if the key file is missing.
    """
    expanded = os.path.expanduser(service_account_file)

    if not os.path.exists(expanded):
        raise FileNotFoundError(
            f"Service account key not found at: {expanded}\n"
            "Set SERVICE_ACCOUNT_FILE in config.py to the correct path."
        )

    creds = Credentials.from_service_account_file(expanded, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

def get_content_label(content_type: str) -> str:
    """Return the display label for a content pillar key."""
    return str(CONTENT_TYPES[content_type]["label"])


def get_content_color_id(content_type: str) -> str:
    """Return the Google Calendar color ID for a content pillar key."""
    return str(CONTENT_TYPES[content_type]["color_id"])


def get_content_weight_percent(content_type: str) -> int:
    """Return the configured weight for a content pillar key, as a whole percent."""
    return int(round(float(CONTENT_TYPES[content_type]["weight"]) * 100))


def build_schedule(
    year: int,
    month: int,
    start_at: datetime | None = None,
    routing_mode: str | None = None,
) -> list[ScheduledPost]:
    """
    Build the month's schedule: WHEN always comes from
    scheduling.generate_posting_dates (cadence + posting days + posting
    time), then scheduling.filter_future_dates discards anything scheduled
    before start_at (defaulting to now, in config.TIMEZONE — see
    slot_matcher.now_in_config_timezone, reused here rather than
    introducing a second "what does now mean" convention).

    In "fifo" mode (the default), that's the whole schedule — every post
    gets content_type=None and prompt=None (prompts are explicitly not
    generated in FIFO mode; see
    docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md).

    In "pillar" mode, WHAT pillar comes from scheduling.allocate_pillars
    (largest remainder) + distribute_pillars (spread pillars evenly rather
    than clustered), computed against the *filtered* future-only count — a
    month already partly in the past is never allocated against its full
    original size. Prompts, if enabled, are attached last and rotate
    sequentially per pillar — they never influence the date or pillar
    decision.

    start_at/routing_mode are injectable so this stays deterministic/
    testable; pass them explicitly in tests, leave both None in normal use
    (routing_mode then defaults to config.ROUTING_MODE).
    """
    mode = routing_mode if routing_mode is not None else ROUTING_MODE

    dates = generate_posting_dates(year, month, POSTS_PER_WEEK, POSTING_DAYS, POSTING_TIME)
    boundary = start_at if start_at is not None else now_in_config_timezone()
    dates = filter_future_dates(dates, boundary)

    if mode == "fifo":
        return [ScheduledPost(scheduled_at=dt, content_type=None, prompt=None) for dt in dates]

    pillar_weights = {key: info["weight"] for key, info in CONTENT_TYPES.items()}
    counts = allocate_pillars(len(dates), pillar_weights)
    pillar_sequence = distribute_pillars(counts)

    prompt_usage: dict[str, int] = {key: 0 for key in CONTENT_TYPES}
    schedule: list[ScheduledPost] = []

    for scheduled_at, content_type in zip(dates, pillar_sequence):
        prompt = None
        if PROMPT_GENERATION_ENABLED:
            prompts_list = PROMPTS.get(content_type) or []
            if prompts_list:
                prompt = prompts_list[prompt_usage[content_type] % len(prompts_list)]
                prompt_usage[content_type] += 1

        schedule.append(ScheduledPost(scheduled_at=scheduled_at, content_type=content_type, prompt=prompt))

    return schedule


# ---------------------------------------------------------------------------
# Calendar event creation
# ---------------------------------------------------------------------------

def build_event_body(post: ScheduledPost) -> dict:
    """Construct the Google Calendar event dict for a single post.

    A FIFO post (content_type is None) has no pillar label or color to draw
    on: title is the fixed FIFO_EVENT_SUMMARY and colorId is omitted
    entirely (the calendar's default color applies) rather than passed as
    None, which the Calendar API would reject."""
    end_dt = post.scheduled_at + timedelta(minutes=EVENT_DURATION_MINUTES)

    body = {
        "summary": f"POST — {get_content_label(post.content_type)}" if post.content_type else FIFO_EVENT_SUMMARY,
        "description": post.prompt or "",
        "start": {
            "dateTime": post.scheduled_at.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
    }
    if post.content_type:
        body["colorId"] = get_content_color_id(post.content_type)
    return body


def push_events(service, calendar_id: str, schedule: list[ScheduledPost], store: ContentStore) -> tuple[int, int]:
    """
    Insert all scheduled posts as Google Calendar events, and mirror each one
    into content_slots for process_content.py to route videos against.

    Returns (events_created, slots_created). slots_created is smaller than
    events_created only when re-running generate_calendar.py for a month
    that already has persisted slots — insert_slot_if_missing skips those
    rather than duplicating or overwriting them (content_slots is unique on
    scheduled_at alone; see docs/decisions/0002-...), so re-running with a
    changed strategy never mutates a slot that already exists. Google
    Calendar events themselves can still duplicate on rerun — that is
    pre-existing behavior, unchanged here.
    """
    created = 0
    slots_created = 0

    for post in schedule:
        event_body = build_event_body(post)
        try:
            event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
            created += 1
            label = get_content_label(post.content_type) if post.content_type else FIFO_EVENT_SUMMARY
            print(f"  Created: {post.scheduled_at.strftime('%a %b %d, %I:%M %p')} — {label}")
        except HttpError as err:
            print(f"  ERROR on {post.scheduled_at}: {err}", file=sys.stderr)
            continue

        was_new = store.insert_slot_if_missing(
            scheduled_at=post.scheduled_at.isoformat(),
            pillar_key=post.content_type,
            prompt=post.prompt,
            created_at=datetime.now(timezone.utc).isoformat(),
            google_calendar_event_id=event.get("id"),
        )
        if was_new:
            slots_created += 1

    return created, slots_created


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

def print_summary(
    schedule: list[ScheduledPost],
    month: int,
    year: int,
    calendar_id: str,
    dry_run: bool = False,
) -> None:
    """Print a formatted post-count summary. FIFO schedules (content_type is
    None throughout) have no pillar allocation to report, only a total;
    pillar schedules keep the existing per-pillar breakdown."""
    month_name = calendar.month_name[month]
    total = len(schedule)
    is_fifo = total == 0 or all(post.content_type is None for post in schedule)

    if is_fifo:
        print(f"\n{month_name} {year} Content Automation — FIFO")
        print("=" * 49)
        print(f"  {'Total:':<28} {total:2d} posts")
        print()
        print(f"  Cadence: {POSTS_PER_WEEK} post(s)/week, posting days: {POSTING_DAYS}, time: {POSTING_TIME}.")
        if dry_run:
            print(f"  Dry run only; no events were pushed to {calendar_id}.")
        else:
            print(f"  Events pushed to {calendar_id} calendar.")
        return

    counts: dict[str, int] = {}
    for post in schedule:
        counts[post.content_type] = counts.get(post.content_type, 0) + 1

    allocation_label = "/".join(str(get_content_weight_percent(ct)) for ct in CONTENT_TYPES)

    print(f"\n{month_name} {year} Content Automation — {allocation_label} Allocation")
    print("=" * 49)
    for ct in CONTENT_TYPES:
        count = counts.get(ct, 0)
        label = get_content_label(ct)
        pct = get_content_weight_percent(ct)
        print(f"  {label + ':':<34} {count:2d} posts  ({pct}%)")
    print(f"  {'Total:':<28} {total:2d} posts")
    print()
    print(f"  Cadence: {POSTS_PER_WEEK} post(s)/week, posting days: {POSTING_DAYS}, time: {POSTING_TIME}.")
    if dry_run:
        print(f"  Dry run only; no events were pushed to {calendar_id}.")
    else:
        print(f"  Events pushed to {calendar_id} calendar.")
