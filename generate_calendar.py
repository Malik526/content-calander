"""
generate_calendar.py — Content calendar generator for MoreClientsCo.

What it does:
  Generates a full month of content calendar events from a configurable
  posting cadence (posts/week, posting-day strategy, posting time) and
  per-pillar percentage weights, and pushes each event to a dedicated,
  app-owned Google Calendar (created/reused via calendar_manager.py, OAuth
  — never "primary"). Prompts are rotated sequentially per content type so
  no prompt repeats until all in its list have been used. See
  docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md
  and docs/decisions/0004-dedicated-google-calendar-ownership.md.

Run command:
  python3 generate_calendar.py --month 06 --year 2026
  python3 generate_calendar.py --month 06 --year 2026 --dry-run

  Advanced/debug override — targets an explicit calendar via the shared
  service account instead of the dedicated app calendar; never the default:
  python3 generate_calendar.py --month 06 --year 2026 --calendar <calendar_id>

Dependencies:
  google-auth, google-auth-oauthlib, google-api-python-client  (see requirements.txt)
  config.py, prompts.py, scheduling.py, content_store.py, calendar_manager.py in the same directory
"""

import argparse
import calendar
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import calendar_manager
from config import (
    CONTENT_TYPES,
    DEFAULT_MONTH,
    DEFAULT_YEAR,
    EVENT_DURATION_MINUTES,
    POSTING_DAYS,
    POSTING_TIME,
    POSTS_PER_WEEK,
    PROMPT_GENERATION_ENABLED,
    SCOPES,
    SERVICE_ACCOUNT_FILE,
    TIMEZONE,
)
from content_store import ContentStore
from prompts import PROMPTS
from scheduling import (
    ScheduleConfigError,
    allocate_pillars,
    distribute_pillars,
    generate_posting_dates,
    validate_schedule_config,
)


# ---------------------------------------------------------------------------
# Data structure for a single scheduled post
# ---------------------------------------------------------------------------

class ScheduledPost(NamedTuple):
    """One posting slot: the datetime, content pillar key, and optional prompt."""
    scheduled_at: datetime
    content_type: str
    prompt: str | None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI flags for month, year, target calendar, and dry-run mode."""
    parser = argparse.ArgumentParser(
        description="Generate a monthly content calendar and push to Google Calendar."
    )
    parser.add_argument(
        "--month",
        type=int,
        default=DEFAULT_MONTH,
        help="Month number (1–12). Default: %(default)s",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=DEFAULT_YEAR,
        help="Four-digit year. Default: %(default)s",
    )
    parser.add_argument(
        "--calendar",
        default=None,
        help=(
            "Advanced/debug override: an explicit Google Calendar ID to write events to, "
            "via the shared service account. Not the default — with this omitted, events go "
            "to the dedicated app-owned 'Content Automation' calendar (created/reused via OAuth)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print the schedule summary without pushing events.",
    )
    return parser.parse_args()


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


def build_schedule(year: int, month: int) -> list[ScheduledPost]:
    """
    Build the month's schedule: WHEN comes from scheduling.generate_posting_dates
    (cadence + posting days + posting time); WHAT pillar comes from
    scheduling.allocate_pillars (largest remainder) + distribute_pillars
    (spread pillars evenly rather than clustered). Prompts, if enabled, are
    attached last and rotate sequentially per pillar — they never influence
    the date or pillar decision.
    """
    dates = generate_posting_dates(year, month, POSTS_PER_WEEK, POSTING_DAYS, POSTING_TIME)

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
    """Construct the Google Calendar event dict for a single post."""
    end_dt = post.scheduled_at + timedelta(minutes=EVENT_DURATION_MINUTES)

    return {
        "summary": f"POST — {get_content_label(post.content_type)}",
        "description": post.prompt or "",
        "start": {
            "dateTime": post.scheduled_at.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
        "colorId": get_content_color_id(post.content_type),
    }


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
            print(
                f"  Created: {post.scheduled_at.strftime('%a %b %d, %I:%M %p')} — "
                f"{get_content_label(post.content_type)}"
            )
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
    """Print a formatted post-count summary reflecting the configured allocation."""
    counts: dict[str, int] = {}
    for post in schedule:
        counts[post.content_type] = counts.get(post.content_type, 0) + 1

    month_name = calendar.month_name[month]
    total = sum(counts.values())
    allocation_label = "/".join(str(get_content_weight_percent(ct)) for ct in CONTENT_TYPES)

    print(f"\n{month_name} {year} Content Calendar — {allocation_label} Allocation")
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Orchestrate arg parsing, schedule generation, and calendar push."""
    args = parse_args()

    # --- Validate month/year range ---
    if not (1 <= args.month <= 12):
        print(f"ERROR: --month must be 1–12 (got {args.month})", file=sys.stderr)
        sys.exit(1)
    if args.year < 2000:
        print(f"ERROR: --year looks wrong (got {args.year})", file=sys.stderr)
        sys.exit(1)

    # --- Validate posting-cadence/pillar-weight configuration up front ---
    pillar_weights = {key: info["weight"] for key, info in CONTENT_TYPES.items()}
    try:
        validate_schedule_config(POSTS_PER_WEEK, POSTING_DAYS, POSTING_TIME, pillar_weights)
    except ScheduleConfigError as err:
        print(f"ERROR: invalid scheduling configuration: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerating {calendar.month_name[args.month]} {args.year} content calendar …")
    if args.calendar:
        print(f"Calendar target: {args.calendar} (explicit --calendar override, service account)\n")
    else:
        print("Calendar target: dedicated app-owned 'Content Automation' calendar (resolved at run time)\n")

    # --- Build schedule ---
    schedule = build_schedule(args.year, args.month)

    if args.dry_run:
        print("Dry run enabled; no Google Calendar events were created, no calendar was resolved/created.")
        print_summary(schedule, args.month, args.year, args.calendar or "(dedicated app calendar — not resolved during dry run)", dry_run=True)
        return

    # --- Connect to Google Calendar ---
    if args.calendar:
        # Explicit advanced/debug override: existing service-account path, unchanged.
        try:
            service = build_calendar_service(SERVICE_ACCOUNT_FILE)
        except FileNotFoundError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            sys.exit(1)
        calendar_id = args.calendar
    else:
        # Normal path: OAuth as the human owner, dedicated app calendar only.
        try:
            service = calendar_manager.build_oauth_calendar_service()
            calendar_id = calendar_manager.resolve_app_calendar(service)
        except calendar_manager.CalendarAuthError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            sys.exit(1)
        print(f"Using dedicated app calendar: {calendar_id}\n")

    # --- Push all events and mirror them into content_slots ---
    with ContentStore() as store:
        events_created, slots_created = push_events(service, calendar_id, schedule, store)
    print(f"\n  {slots_created} new content_slots persisted ({events_created} calendar events created).")

    # --- Final summary ---
    print_summary(schedule, args.month, args.year, calendar_id)


if __name__ == "__main__":
    main()
