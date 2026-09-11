"""
generate_calendar.py — Content calendar generator for MoreClientsCo.

What it does:
  Generates a full month of content calendar events based on a fixed weekly
  posting schedule and pushes each event to Google Calendar via a service
  account. Prompts are rotated sequentially per content type so no prompt
  repeats until all in its list have been used.

Run command:
  python3 generate_calendar.py --month 06 --year 2026
  python3 generate_calendar.py --month 06 --year 2026 --calendar <calendar_id>

Dependencies:
  google-auth, google-api-python-client  (see requirements.txt)
  config.py, prompts.py in the same directory
"""

import argparse
import calendar
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    CONTENT_TYPES,
    DEFAULT_CALENDAR_ID,
    DEFAULT_MONTH,
    DEFAULT_YEAR,
    EVENT_DURATION_MINUTES,
    EVENT_START_HOUR,
    FIFTH_SUNDAY_CONTENT_TYPE,
    SCOPES,
    SERVICE_ACCOUNT_FILE,
    TIMEZONE,
    WEEKLY_SCHEDULE,
)
from content_store import ContentStore
from prompts import PROMPTS


# ---------------------------------------------------------------------------
# Data structure for a single scheduled post
# ---------------------------------------------------------------------------

class ScheduledPost(NamedTuple):
    """One posting slot: the date, content pillar key, and prompt to use."""
    day: date
    content_type: str
    prompt: str


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
        default=DEFAULT_CALENDAR_ID,
        help="Google Calendar ID to write events to. Default: %(default)s",
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

def get_month_dates(year: int, month: int) -> list[date]:
    """Return a list of every date in the given month."""
    _, days_in_month = calendar.monthrange(year, month)
    return [date(year, month, day) for day in range(1, days_in_month + 1)]


def get_content_type_for_date(day: date, sunday_count: int) -> str:
    """Return the content pillar key assigned to a date."""
    weekday_name = calendar.day_name[day.weekday()].lower()

    if weekday_name == "sunday" and sunday_count == 5:
        return FIFTH_SUNDAY_CONTENT_TYPE

    return WEEKLY_SCHEDULE[weekday_name]


def get_content_label(content_type: str) -> str:
    """Return the display label for a content pillar key."""
    return str(CONTENT_TYPES[content_type]["label"])


def get_content_color_id(content_type: str) -> str:
    """Return the Google Calendar color ID for a content pillar key."""
    return str(CONTENT_TYPES[content_type]["color_id"])


def get_content_target_percent(content_type: str) -> int:
    """Return the target allocation percentage for a content pillar key."""
    return int(float(CONTENT_TYPES[content_type]["target_percent"]) * 100)


def build_schedule(dates: list[date]) -> list[ScheduledPost]:
    """
    Map each date to its content type and prompt.

    - Each date uses the day-name mapping from WEEKLY_SCHEDULE.
    - A fifth Sunday is assigned to Agency Execution to rebalance the month.
    - Prompts rotate sequentially per content type; wraps only after
      all prompts in the list have been used once.
    """
    # Tracks how many times each content type has been assigned (drives prompt index)
    usage: dict[str, int] = {ct: 0 for ct in PROMPTS}
    sunday_count = 0
    schedule: list[ScheduledPost] = []

    for d in dates:
        # --- Resolve content type ---
        if d.weekday() == 6:
            sunday_count += 1
        content_type = get_content_type_for_date(d, sunday_count)

        # --- Pick next prompt, cycling through list without repeating ---
        prompts_list = PROMPTS[content_type]
        prompt_index = usage[content_type] % len(prompts_list)
        prompt = prompts_list[prompt_index]
        usage[content_type] += 1

        schedule.append(ScheduledPost(day=d, content_type=content_type, prompt=prompt))

    return schedule


# ---------------------------------------------------------------------------
# Calendar event creation
# ---------------------------------------------------------------------------

def slot_start_datetime(post: ScheduledPost) -> datetime:
    """Naive local wall-clock start time (in TIMEZONE) for a scheduled post.

    Shared by build_event_body (Google Calendar) and push_events
    (content_slots) so both always agree on the exact scheduled_at value.
    """
    return datetime(
        post.day.year, post.day.month, post.day.day,
        EVENT_START_HOUR, 0, 0
    )


def build_event_body(post: ScheduledPost) -> dict:
    """
    Construct the Google Calendar event dict for a single post.

    Uses dateTime (not all-day) so the block shows at 9:00–9:30 AM.
    """
    start_dt = slot_start_datetime(post)
    end_dt = start_dt + timedelta(minutes=EVENT_DURATION_MINUTES)

    return {
        "summary": f"POST — {get_content_label(post.content_type)}",
        "description": post.prompt,
        "start": {
            "dateTime": start_dt.isoformat(),
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
    rather than duplicating them, so content_slots stays idempotent even
    though re-running still creates duplicate Google Calendar events (existing,
    unchanged behavior).
    """
    created = 0
    slots_created = 0

    for post in schedule:
        event_body = build_event_body(post)
        try:
            event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
            created += 1
            print(
                f"  Created: {post.day.strftime('%a %b %d')} — "
                f"{get_content_label(post.content_type)}"
            )
        except HttpError as err:
            print(f"  ERROR on {post.day}: {err}", file=sys.stderr)
            continue

        was_new = store.insert_slot_if_missing(
            scheduled_at=slot_start_datetime(post).isoformat(),
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
    """Print a formatted post-count summary reflecting the revised allocation."""
    counts: dict[str, int] = {}
    for post in schedule:
        counts[post.content_type] = counts.get(post.content_type, 0) + 1

    month_name = calendar.month_name[month]
    total = sum(counts.values())

    print(f"\n{month_name} {year} Content Calendar — 40/25/20/15 Allocation")
    print("=" * 49)
    for ct in CONTENT_TYPES:
        count = counts.get(ct, 0)
        label = get_content_label(ct)
        pct = get_content_target_percent(ct)
        print(f"  {label + ':':<34} {count:2d} posts  ({pct}%)")
    print(f"  {'Total:':<28} {total:2d} posts")
    print()
    print("  Fifth Sundays are assigned to Agency Execution for rebalancing.")
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

    print(f"\nGenerating {calendar.month_name[args.month]} {args.year} content calendar …")
    print(f"Calendar target: {args.calendar}\n")

    # --- Build schedule ---
    dates = get_month_dates(args.year, args.month)
    schedule = build_schedule(dates)

    if args.dry_run:
        print("Dry run enabled; no Google Calendar events were created.")
        print_summary(schedule, args.month, args.year, args.calendar, dry_run=True)
        return

    # --- Connect to Google Calendar ---
    try:
        service = build_calendar_service(SERVICE_ACCOUNT_FILE)
    except FileNotFoundError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Push all events and mirror them into content_slots ---
    with ContentStore() as store:
        events_created, slots_created = push_events(service, args.calendar, schedule, store)
    print(f"\n  {slots_created} new content_slots persisted ({events_created} calendar events created).")

    # --- Final summary ---
    print_summary(schedule, args.month, args.year, args.calendar)


if __name__ == "__main__":
    main()
