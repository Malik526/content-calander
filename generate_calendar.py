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
from datetime import date, datetime, timedelta
from typing import NamedTuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    COLOR_IDS,
    CONTENT_ALLOCATION,
    DEFAULT_CALENDAR_ID,
    DEFAULT_MONTH,
    DEFAULT_YEAR,
    EVENT_DURATION_MINUTES,
    EVENT_START_HOUR,
    SCOPES,
    SERVICE_ACCOUNT_FILE,
    SUNDAY_ROTATION,
    TIMEZONE,
    WEEKLY_SCHEDULE,
)
from prompts import PROMPTS


# ---------------------------------------------------------------------------
# Data structure for a single scheduled post
# ---------------------------------------------------------------------------

class ScheduledPost(NamedTuple):
    """One posting slot: the date, content type, and prompt to use."""
    day: date
    content_type: str
    prompt: str


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse --month, --year, and optional --calendar CLI flags."""
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


def build_schedule(dates: list[date]) -> list[ScheduledPost]:
    """
    Map each date to its content type and prompt.

    - Weekdays 0–5 use WEEKLY_SCHEDULE directly.
    - Sundays (weekday 6) alternate between SUNDAY_ROTATION entries,
      starting with index 0 on the first Sunday of the month.
    - Prompts rotate sequentially per content type; wraps only after
      all prompts in the list have been used once.
    """
    # Tracks how many times each content type has been assigned (drives prompt index)
    usage: dict[str, int] = {ct: 0 for ct in PROMPTS}
    sunday_count = 0
    schedule: list[ScheduledPost] = []

    for d in dates:
        weekday = d.weekday()  # 0=Mon … 6=Sun

        # --- Resolve content type ---
        if weekday == 6:
            content_type = SUNDAY_ROTATION[sunday_count % len(SUNDAY_ROTATION)]
            sunday_count += 1
        else:
            content_type = WEEKLY_SCHEDULE[weekday]

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

def build_event_body(post: ScheduledPost) -> dict:
    """
    Construct the Google Calendar event dict for a single post.

    Uses dateTime (not all-day) so the block shows at 9:00–9:30 AM.
    """
    start_dt = datetime(
        post.day.year, post.day.month, post.day.day,
        EVENT_START_HOUR, 0, 0
    )
    end_dt = start_dt + timedelta(minutes=EVENT_DURATION_MINUTES)

    description = post.prompt
    # Append workshop CTA to all Educational posts to support the monthly funnel
    if post.content_type == "Educational":
        description += (
            "\n\nCTA: End this post with:\n"
            "'I cover this live in my free monthly workshop "
            "for service business owners. Link in bio to register.'"
        )

    return {
        "summary": f"POST — {post.content_type}",
        "description": description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": TIMEZONE,
        },
        "colorId": COLOR_IDS[post.content_type],
    }


def push_events(service, calendar_id: str, schedule: list[ScheduledPost]) -> int:
    """
    Insert all scheduled posts as Google Calendar events.

    Returns the count of successfully created events.
    """
    created = 0

    for post in schedule:
        event_body = build_event_body(post)
        try:
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
            created += 1
            print(f"  Created: {post.day.strftime('%a %b %d')} — {post.content_type}")
        except HttpError as err:
            print(f"  ERROR on {post.day}: {err}", file=sys.stderr)

    return created


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

def print_summary(
    schedule: list[ScheduledPost], month: int, year: int, calendar_id: str
) -> None:
    """Print a formatted post-count summary reflecting the revised allocation."""
    counts: dict[str, int] = {}
    for post in schedule:
        counts[post.content_type] = counts.get(post.content_type, 0) + 1

    month_name = calendar.month_name[month]
    total = sum(counts.values())

    # Print in allocation-rank order
    ordered_types = [
        "Building Systems",
        "Educational",
        "Entrepreneurship Journey",
        "Personal Transformation",
    ]

    print(f"\n{month_name} {year} Content Calendar — Revised Allocation")
    print("=" * 49)
    for ct in ordered_types:
        count = counts.get(ct, 0)
        pct = int(CONTENT_ALLOCATION.get(ct, 0) * 100)
        print(f"  {ct + ':':<28} {count:2d} posts  ({pct}%)")
    print(f"  {'Total:':<28} {total:2d} posts")
    print()
    print("  Workshop CTA included on all Educational posts.")
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

    # --- Connect to Google Calendar ---
    try:
        service = build_calendar_service(SERVICE_ACCOUNT_FILE)
    except FileNotFoundError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    # --- Push all events ---
    push_events(service, args.calendar, schedule)

    # --- Final summary ---
    print_summary(schedule, args.month, args.year, args.calendar)


if __name__ == "__main__":
    main()
