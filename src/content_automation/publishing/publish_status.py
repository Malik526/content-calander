"""
publish_status.py — the one platform-neutral resolver from stored publishing
truth to user-facing publish state (Milestone 3.11: User-Facing Publish
States + Errors).

What it does:
  resolve_slot_publish_status(slot, posts) turns a content_slot and its
  assigned video's platform_posts rows into what the Queue shows:

    OPEN             slot has no video.
    SCHEDULED        platform post PENDING and not yet overdue (including a
                     PENDING row waiting on an automatic retry).
    PUBLISHING       PUBLISHING and still progressing.
    PUBLISHED        PUBLISHED with a platform post id (confirmed by the
                     platform).
    FAILED           FAILED (terminal) — explained via failure_taxonomy
                     from platform_posts.failure_code, never failure_reason.
    NEEDS_ATTENTION  anything uncertain or contradictory, rather than a
                     guessed PUBLISHED/FAILED:
      SCHEDULE_MISSED       PENDING, scheduled time passed more than
                            config.PUBLISH_OVERDUE_GRACE_MINUTES ago, no
                            future retry pending (nothing published it —
                            e.g. no worker ran).
      PUBLISH_STALLED       PUBLISHING with no platform post id, not
                            updated for config.PLATFORM_POST_STALE_MINUTES
                            (a claim that never reached the platform — the
                            same staleness rule crash_recovery.py uses).
      PUBLISH_UNCONFIRMED   PUBLISHING with a platform post id whose status
                            check is overdue by that same threshold (the
                            platform accepted it; the outcome is unknown).
      STATE_INCONSISTENT    PUBLISHED without a platform post id, an
                            unrecognized status value, or an ASSIGNED slot
                            with no video.
      NOT_SET_UP_TO_PUBLISH assigned video with no platform_posts row at
                            all (nothing would ever publish it).

  Multiple platforms: each post resolves independently (publications), and
  the slot shows the most urgent: NEEDS_ATTENTION > FAILED > PUBLISHING >
  SCHEDULED > PUBLISHED — PUBLISHED only when every post is published.

  An EDIT_CAPTION hint is dropped once the caption is locked
  (media.caption_editing.post_locks_caption) — never suggest an action the
  API would refuse.

  Read-only: nothing here writes, retries or reconciles (Milestone 3.13).
  content_slots.status is still never written past ASSIGNED (ADR-0013).

Dependencies:
  persistence.content_store records, publishing.failure_taxonomy,
  media.caption_editing (lock rule), config.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from content_automation.config import PLATFORM_POST_STALE_MINUTES, PUBLISH_OVERDUE_GRACE_MINUTES, TIMEZONE
from content_automation.media.caption_editing import post_locks_caption
from content_automation.persistence.content_store import PlatformPostRecord, SlotRecord
from content_automation.publishing.failure_taxonomy import EDIT_CAPTION, describe_failure, platform_label

OPEN = "OPEN"
SCHEDULED = "SCHEDULED"
PUBLISHING = "PUBLISHING"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"
NEEDS_ATTENTION = "NEEDS_ATTENTION"

_URGENCY = [NEEDS_ATTENTION, FAILED, PUBLISHING, SCHEDULED, PUBLISHED]

_ATTENTION_MESSAGES = {
    "SCHEDULE_MISSED": "The scheduled time passed without this being published.",
    "PUBLISH_STALLED": "Publishing started but didn't finish.",
    "PUBLISH_UNCONFIRMED": "Publishing status could not be confirmed.",
    "STATE_INCONSISTENT": "Publishing status is unclear.",
    "NOT_SET_UP_TO_PUBLISH": "This video isn't set up to publish to any platform.",
}


@dataclass(frozen=True)
class PublicationStatus:
    platform: str
    display_status: str
    platform_post_status: str
    published_at: str | None
    # A failure category (failure_taxonomy) or an attention code (above).
    reason_code: str | None = None
    message: str | None = None
    action_hint: str | None = None


@dataclass(frozen=True)
class SlotPublishStatus:
    display_status: str
    reason_code: str | None = None
    message: str | None = None
    action_hint: str | None = None
    published_at: str | None = None
    # Mirrors ContentStore.unassign_slot's guard: only while every post is PENDING.
    can_unassign: bool = False
    publications: list[PublicationStatus] = field(default_factory=list)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _attention(post: PlatformPostRecord, code: str) -> PublicationStatus:
    return PublicationStatus(
        platform=post.platform, display_status=NEEDS_ATTENTION, platform_post_status=post.status,
        published_at=post.published_at, reason_code=code, message=_ATTENTION_MESSAGES[code],
    )


def _local_now(tz_name: str | None, now_utc: datetime) -> datetime:
    """now as naive wall-clock time in tz_name (scheduled_at/next_retry_at's convention)."""
    try:
        tz = ZoneInfo(tz_name or TIMEZONE)
    except (KeyError, ValueError):
        tz = ZoneInfo(TIMEZONE)
    return now_utc.astimezone(tz).replace(tzinfo=None)


def resolve_publication_status(
    post: PlatformPostRecord, *, scheduled_at: str | None, slot_timezone: str | None, now_utc: datetime,
) -> PublicationStatus:
    stale = timedelta(minutes=PLATFORM_POST_STALE_MINUTES)

    if post.status == "PENDING":
        next_retry = _parse(post.next_retry_at)
        retry_pending = next_retry is not None and next_retry > _local_now(TIMEZONE, now_utc)
        due = _parse(scheduled_at)
        overdue = due is not None and due + timedelta(minutes=PUBLISH_OVERDUE_GRACE_MINUTES) < _local_now(slot_timezone, now_utc)
        if overdue and not retry_pending:
            return _attention(post, "SCHEDULE_MISSED")
        if post.retry_count > 0:
            return PublicationStatus(
                platform=post.platform, display_status=SCHEDULED, platform_post_status=post.status,
                published_at=None, reason_code=describe_failure(post.platform, post.failure_code).category,
                message="A previous attempt didn't go through; another attempt is scheduled.",
            )
        return PublicationStatus(platform=post.platform, display_status=SCHEDULED, platform_post_status=post.status, published_at=None)

    if post.status == "PUBLISHING":
        if post.platform_post_id is None:
            last_progress = _parse(post.updated_at)
            if last_progress is None or last_progress + stale < now_utc:
                return _attention(post, "PUBLISH_STALLED")
        else:
            check_due = _parse(post.next_status_check_at) or _parse(post.updated_at)
            if check_due is None or check_due + stale < now_utc:
                return _attention(post, "PUBLISH_UNCONFIRMED")
        return PublicationStatus(
            platform=post.platform, display_status=PUBLISHING, platform_post_status=post.status, published_at=None,
            message=f"Sending to {platform_label(post.platform)}…",
        )

    if post.status == "PUBLISHED":
        if not post.platform_post_id:
            return _attention(post, "STATE_INCONSISTENT")
        return PublicationStatus(
            platform=post.platform, display_status=PUBLISHED, platform_post_status=post.status, published_at=post.published_at,
        )

    if post.status == "FAILED":
        description = describe_failure(post.platform, post.failure_code)
        hint = description.action_hint
        if hint == EDIT_CAPTION and post_locks_caption(post):
            # The platform already received this caption; it can't be edited now.
            hint = None
        return PublicationStatus(
            platform=post.platform, display_status=FAILED, platform_post_status=post.status, published_at=None,
            reason_code=description.category, message=description.message, action_hint=hint,
        )

    return _attention(post, "STATE_INCONSISTENT")


def resolve_slot_publish_status(
    slot: SlotRecord, posts: list[PlatformPostRecord], *, now_utc: datetime | None = None,
) -> SlotPublishStatus:
    now_utc = now_utc or datetime.now(timezone.utc)

    if slot.status == "OPEN" and slot.assigned_video_id is None:
        return SlotPublishStatus(display_status=OPEN)
    if slot.status != "ASSIGNED" or slot.assigned_video_id is None:
        return SlotPublishStatus(
            display_status=NEEDS_ATTENTION, reason_code="STATE_INCONSISTENT",
            message=_ATTENTION_MESSAGES["STATE_INCONSISTENT"],
        )
    if not posts:
        return SlotPublishStatus(
            display_status=NEEDS_ATTENTION, reason_code="NOT_SET_UP_TO_PUBLISH",
            message=_ATTENTION_MESSAGES["NOT_SET_UP_TO_PUBLISH"], can_unassign=True,
        )

    publications = [
        resolve_publication_status(post, scheduled_at=slot.scheduled_at, slot_timezone=slot.timezone, now_utc=now_utc)
        for post in posts
    ]
    can_unassign = all(post.status == "PENDING" for post in posts)

    for status in _URGENCY[:-1]:
        driver = next((p for p in publications if p.display_status == status), None)
        if driver is not None:
            return SlotPublishStatus(
                display_status=status, reason_code=driver.reason_code, message=driver.message,
                action_hint=driver.action_hint, can_unassign=can_unassign, publications=publications,
            )

    # Every post is PUBLISHED.
    latest = max((p.published_at for p in publications if p.published_at), default=None)
    return SlotPublishStatus(display_status=PUBLISHED, published_at=latest, can_unassign=False, publications=publications)
