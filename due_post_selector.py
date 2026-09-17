"""
due_post_selector.py — Deterministic due-post detection for TikTok Direct
Post publishing (Milestone 2.1.1).

What it does:
  Answers exactly one question: given the database and a supplied/current
  time, which platform_posts rows are due for execution right now? This is
  read/selection logic only — it never claims, mutates, or publishes
  anything. Ownership/claiming, worker loops, retries, and crash recovery
  are explicitly out of scope here; see
  docs/evaluations/scheduling/milestone-2.1.1-due-post-detection.md for the
  full validation record, and
  docs/decisions/0006-tiktok-publisher-foundation.md for the platform_posts
  schema/state machine this reads.

  Mirrors slot_matcher.py's shape deliberately: deterministic,
  content_store-dependent, one selection concern, an optional injected
  `now` for tests. Same architecture, opposite end of the pipeline —
  slot_matcher.py decides which content_slot a video goes into;
  this decides which already-scheduled platform_posts row is due to be
  worked on next.

Eligible statuses:
  PENDING (never yet submitted) and PUBLISHING (submitted, not yet
  resolved) — both still "in play". PUBLISHED and FAILED are excluded:
  PUBLISHED is a completed post, and FAILED is documented in
  publish_tiktok.py's module docstring as "left as a terminal FAILED
  record rather than silently retried" — this selector treats it the
  same way. (Note: publish_tiktok.py's own manual-rerun path does not
  currently re-check status before re-polling an already-FAILED row with
  a platform_post_id set — a pre-existing quirk this milestone did not
  touch. See the evaluation doc.)

Known architectural gap (a read-only finding from Milestone 2.1.1, not
fixed here — see the evaluation doc for the proposed smallest-compatible
follow-up):
  publish_tiktok.py only creates a platform_posts row at the moment it is
  manually invoked for a video — nothing pre-creates a PENDING row when a
  video is assigned a content_slot. Today, this selector is functionally
  correct but will return nothing for a video that has never had
  publish_tiktok.py run for it at least once, even if that video's
  content_slot.scheduled_at is already in the past.

Dependencies:
  content_store.py, slot_matcher.now_in_config_timezone (reused, not
  duplicated — same naive-local-time convention as content_slots.scheduled_at,
  which platform_posts.scheduled_at is copied from at submission time).
"""

from datetime import datetime

from content_store import ContentStore, PlatformPostRecord
from slot_matcher import now_in_config_timezone

ELIGIBLE_STATUSES = ["PENDING", "PUBLISHING"]


def get_due_posts(store: ContentStore, platform: str, now: datetime | None = None) -> list[PlatformPostRecord]:
    """Due platform_posts rows for `platform`: scheduled_at is set and at
    or before `now` (inclusive — a post scheduled exactly at `now` is
    due), status is still PENDING or PUBLISHING. Returned earliest-
    scheduled first, ties broken by id, for deterministic ordering. Pure
    read; never mutates a row.

    `now` defaults to the current time in config.TIMEZONE, naive (no
    tzinfo) — matching how scheduled_at is stored (see
    slot_matcher.now_in_config_timezone). Pass a fixed naive datetime in
    tests rather than relying on wall-clock time.
    """
    resolved_now = now if now is not None else now_in_config_timezone()
    return store.get_due_platform_posts(platform, resolved_now.isoformat(), ELIGIBLE_STATUSES)
