"""
clear_calendar.py — Safely clears the dedicated app-owned Google Calendar's
managed schedule, and the matching content_slots, without ever touching the
user's primary or any other calendar.

Default scope is OPEN (unassigned) slots only — never-yet-used future
posting opportunities. Use --all to also clear ASSIGNED slots (destructive:
resets those videos back to CLASSIFIED so they can be rescheduled). See
docs/decisions/0004-dedicated-google-calendar-ownership.md.

Usage:
    python3 clear_calendar.py                  # clear OPEN slots + their events
    python3 clear_calendar.py --dry-run         # report only, no mutation
    python3 clear_calendar.py --all             # also clear ASSIGNED slots + their events
    python3 clear_calendar.py --all --dry-run

Advanced/debug override — full clear of an explicit calendar via the shared
service account, ignoring content_slots/dedicated-calendar scoping entirely
(the tool's original behavior). Never the default; fails closed otherwise:
    python3 clear_calendar.py --calendar <id>
    python3 clear_calendar.py --calendar <id> --start 2026-06-01 --end 2026-07-01

Dependencies:
  calendar_manager.py, content_store.py, generate_calendar.py (service-account
  helper, override path only), config.py
"""

import argparse
import sys

from googleapiclient.errors import HttpError

import calendar_manager
from config import SERVICE_ACCOUNT_FILE
from content_store import ContentStore
from generate_calendar import build_calendar_service


# ---------------------------------------------------------------------------
# Dedicated app calendar path (default)
# ---------------------------------------------------------------------------

def _clear_dedicated_calendar(*, dry_run: bool, include_assigned: bool) -> None:
    try:
        service = calendar_manager.build_oauth_calendar_service()
    except calendar_manager.CalendarAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        calendar_id = calendar_manager.resolve_app_calendar(service, create_if_missing=False)
    except calendar_manager.CalendarStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    statuses = ["OPEN", "ASSIGNED"] if include_assigned else ["OPEN"]

    with ContentStore() as store:
        slots = store.list_slots_by_status(statuses)

        print(f"Target calendar : {calendar_manager.APP_CALENDAR_SUMMARY} ({calendar_id})")
        print(f"Scope           : {'OPEN + ASSIGNED (destructive)' if include_assigned else 'OPEN only'}")
        print(f"Slots to clear  : {len(slots)}\n")

        if dry_run:
            for slot in slots:
                print(f"  would remove: {slot.scheduled_at} [{slot.pillar_key}] status={slot.status}")
            print("\nDry run: nothing was deleted.")
            return

        if not slots:
            print("Nothing to clear.")
            return

        cleared = 0
        for slot in slots:
            if slot.status == "ASSIGNED":
                store.unassign_video_for_slot(slot.id)
            if slot.google_calendar_event_id:
                try:
                    service.events().delete(calendarId=calendar_id, eventId=slot.google_calendar_event_id).execute()
                except HttpError as exc:
                    if exc.resp.status not in (404, 410):  # already gone is fine
                        print(f"  WARNING: could not delete calendar event for {slot.scheduled_at}: {exc}", file=sys.stderr)
            store.delete_slot(slot.id)
            print(f"  Cleared: {slot.scheduled_at} [{slot.pillar_key}]")
            cleared += 1

        print(f"\nCleared {cleared} slot(s) and their calendar events.")


# ---------------------------------------------------------------------------
# Advanced/debug override: explicit calendar, service account (unchanged
# from the tool's original behavior)
# ---------------------------------------------------------------------------

def _list_events(service, calendar_id: str, time_min: str | None, time_max: str | None) -> list:
    events = []
    page_token = None
    kwargs = {"calendarId": calendar_id, "maxResults": 250, "singleEvents": True, "orderBy": "startTime"}
    if time_min:
        kwargs["timeMin"] = time_min
    if time_max:
        kwargs["timeMax"] = time_max

    while True:
        result = service.events().list(pageToken=page_token, **kwargs).execute()
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return events


def _delete_events(service, calendar_id: str, events: list) -> tuple[int, int]:
    deleted = failed = 0
    for event in events:
        summary = event.get("summary", "untitled")
        event_date = event.get("start", {}).get("date") or event.get("start", {}).get("dateTime", "unknown")
        try:
            service.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
            print(f"  Deleted: {summary} on {event_date}")
            deleted += 1
        except Exception as exc:
            print(f"  FAILED:  {summary} on {event_date} — {exc}")
            failed += 1
    return deleted, failed


def _clear_explicit_calendar(calendar_id: str, start: str | None, end: str | None, dry_run: bool) -> None:
    """Advanced/debug override: full clear of an explicitly named calendar via
    the shared service account. Ignores content_slots/dedicated-calendar
    scoping entirely — the caller is responsible for knowing what this
    calendar_id actually is. Never the default path."""
    time_min = f"{start}T00:00:00Z" if start else None
    time_max = f"{end}T00:00:00Z" if end else None

    print(f"\nCalendar : {calendar_id} (explicit override, service account)")
    print(f"Range    : {start or '(no lower bound)'} → {end or '(no upper bound)'}")
    print("-" * 50)

    service = build_calendar_service(SERVICE_ACCOUNT_FILE)

    print("Fetching events…")
    events = _list_events(service, calendar_id, time_min, time_max)
    print(f"Found {len(events)} event(s) to delete\n")

    if dry_run:
        for evt in events:
            print(f"  would delete: {evt.get('summary', 'untitled')} on {evt.get('start', {})}")
        print("\nDry run: nothing was deleted.")
        return

    if not events:
        print("Nothing to delete. Exiting.")
        return

    deleted, failed = _delete_events(service, calendar_id, events)

    print(f"\n{'=' * 50}")
    print(f"  Deleted : {deleted}")
    print(f"  Failed  : {failed}")
    print(f"{'=' * 50}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear the dedicated app-owned Google Calendar's managed schedule."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without deleting anything.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Also clear ASSIGNED slots and their calendar events (not just OPEN ones). "
            "Destructive: resets those videos back to CLASSIFIED so they can be rescheduled."
        ),
    )
    parser.add_argument(
        "--calendar",
        default=None,
        help=(
            "Advanced/debug override: fully clear this explicit calendar ID via the shared "
            "service account, ignoring content_slots entirely. Never the default."
        ),
    )
    parser.add_argument("--start", default=None, help="Only with --calendar: ISO start date, inclusive (e.g. 2026-06-01).")
    parser.add_argument("--end", default=None, help="Only with --calendar: ISO end date, exclusive.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.calendar:
        _clear_explicit_calendar(args.calendar, args.start, args.end, args.dry_run)
        return

    _clear_dedicated_calendar(dry_run=args.dry_run, include_assigned=args.all)


if __name__ == "__main__":
    main()
