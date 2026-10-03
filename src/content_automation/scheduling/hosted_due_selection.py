"""
hosted_due_selection.py — which hosted platform_posts are due right now,
honoring each slot's own timezone (Milestone 3.12: Hosted Scheduler +
Worker Execution).

What it does:
  due_post_selector.get_due_posts compares the naive-local scheduled_at
  against "now" in the single global config.TIMEZONE — correct for the
  local CLI, whose slots are all in that timezone. Hosted slots are
  generated in each user's own cadence timezone (content_slots.timezone,
  Milestone 3.8), so the same comparison would fire a Los Angeles 09:00
  post at 09:00 New York time. This module keeps the existing selector
  and store query and only makes the time comparison exact:

    1. Ask the existing selector with a deliberately late "now" — the
       wall-clock time in the furthest-ahead timezone (UTC+14). Every row
       that is due in *any* timezone is in that superset; it can only
       over-select, never miss.
    2. Keep a row only if scheduled_at <= now in its own slot's timezone
       (falling back to config.TIMEZONE for a slot with none) and
       next_retry_at <= now in config.TIMEZONE — next_retry_at's existing
       storage convention (publish_tiktok._schedule_retry_or_fail writes it
       from slot_matcher.now_in_config_timezone()), deliberately unchanged.

  Pure read; never claims or writes. Claiming stays in
  worker.run_due_posts_once (via its due_posts parameter).

  Milestone 3.14 follow-up (overdue telemetry): lateness_seconds() — how
  far past its scheduled time a post is, in its own slot's timezone —
  feeds the hosted worker's due_backlog / post_claimed / outcome log
  events. Observability only: due-ness itself is unchanged, and there is
  still no lateness cutoff (the provisional V1 catch-up rule — see the
  Milestone 3.14 evaluation record, "Overdue Publishing").

Dependencies:
  scheduling.due_post_selector, persistence.protocol, config.TIMEZONE.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from content_automation.config import TIMEZONE
from content_automation.persistence.content_store import PlatformPostRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.scheduling import due_post_selector

# The largest UTC offset any IANA timezone uses (Pacific/Kiritimati).
_MAX_UTC_OFFSET = timedelta(hours=14)


def _local_now(tz_name: str | None, now_utc: datetime) -> datetime:
    try:
        tz = ZoneInfo(tz_name or TIMEZONE)
    except (KeyError, ValueError):
        tz = ZoneInfo(TIMEZONE)
    return now_utc.astimezone(tz).replace(tzinfo=None)


def _slot_timezone(store: ContentStoreProtocol, post: PlatformPostRecord) -> str | None:
    video = store.get_video(post.video_id)
    if video is None or video.assigned_slot_id is None:
        return None
    slot = store.get_slot(video.assigned_slot_id)
    return slot.timezone if slot is not None else None


def lateness_seconds(store: ContentStoreProtocol, post: PlatformPostRecord, now_utc: datetime) -> int:
    """Whole seconds now_utc is past post.scheduled_at (negative if
    early), with scheduled_at read as wall-clock time in the post's slot
    timezone. Computed between aware instants, so a DST change between the
    two is measured correctly rather than as a wall-clock difference."""
    tz_name = _slot_timezone(store, post)
    try:
        tz = ZoneInfo(tz_name or TIMEZONE)
    except (KeyError, ValueError):
        tz = ZoneInfo(TIMEZONE)
    scheduled = datetime.fromisoformat(post.scheduled_at).replace(tzinfo=tz)
    return int((now_utc - scheduled).total_seconds())


def get_hosted_due_posts(
    store: ContentStoreProtocol, platform: str, user_id: int, now_utc: datetime | None = None,
) -> list[PlatformPostRecord]:
    now_utc = now_utc or datetime.now(timezone.utc)
    latest_wall_clock = (now_utc.astimezone(timezone.utc) + _MAX_UTC_OFFSET).replace(tzinfo=None)
    candidates = due_post_selector.get_due_posts(store, platform, now=latest_wall_clock, user_id=user_id)

    retry_now = _local_now(TIMEZONE, now_utc)
    due = []
    for post in candidates:
        if datetime.fromisoformat(post.scheduled_at) > _local_now(_slot_timezone(store, post), now_utc):
            continue
        if post.next_retry_at is not None and datetime.fromisoformat(post.next_retry_at) > retry_now:
            continue
        due.append(post)
    return due
