"""
cli/generate_calendar.py — Thin CLI entry point for the content-calendar
generator (Milestone 3.0's package refactor thinned this from the
original standalone generate_calendar.py).

All actual logic (build_schedule, push_events, print_summary,
build_calendar_service, and the get_content_* helpers) lives in
content_automation.calendar.generate_calendar — this file only parses
arguments, validates config, and orchestrates the calendar push.

Run:
  python3 cli/generate_calendar.py --month 06 --year 2026
  python3 cli/generate_calendar.py --month 06 --year 2026 --dry-run

  Advanced/debug override — targets an explicit calendar via the shared
  service account instead of the dedicated app calendar; never the default:
  python3 cli/generate_calendar.py --month 06 --year 2026 --calendar <calendar_id>
"""

import argparse
import calendar
import sys

from content_automation.calendar import calendar_manager
from content_automation.calendar.cadence import (
    ScheduleConfigError,
    validate_cadence_config,
    validate_pillar_weights,
    validate_routing_mode,
)
from content_automation.calendar.generate_calendar import build_calendar_service, build_schedule, print_summary, push_events
from content_automation.config import (
    CONTENT_TYPES,
    DEFAULT_MONTH,
    DEFAULT_YEAR,
    POSTING_DAYS,
    POSTING_TIME,
    POSTS_PER_WEEK,
    ROUTING_MODE,
    SERVICE_ACCOUNT_FILE,
)
from content_automation.persistence.content_store import ContentStore


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

    # --- Validate routing mode + posting-cadence configuration up front;
    #     pillar weights only matter (and are only validated) in pillar mode ---
    try:
        validate_routing_mode(ROUTING_MODE)
        validate_cadence_config(POSTS_PER_WEEK, POSTING_DAYS, POSTING_TIME)
        if ROUTING_MODE == "pillar":
            pillar_weights = {key: info["weight"] for key, info in CONTENT_TYPES.items()}
            validate_pillar_weights(pillar_weights)
    except ScheduleConfigError as err:
        print(f"ERROR: invalid scheduling configuration: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerating {calendar.month_name[args.month]} {args.year} content calendar ({ROUTING_MODE} mode) …")
    if args.calendar:
        print(f"Calendar target: {args.calendar} (explicit --calendar override, service account)\n")
    else:
        print("Calendar target: dedicated app-owned 'Content Automation' calendar (resolved at run time)\n")

    # --- Build schedule (future-only: past-dated candidates are discarded
    #     before pillar allocation, so weights apply only to what's left) ---
    schedule = build_schedule(args.year, args.month)

    if not schedule:
        print(f"No future posting slots remain for {calendar.month_name[args.month]} {args.year}.")
        return

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
