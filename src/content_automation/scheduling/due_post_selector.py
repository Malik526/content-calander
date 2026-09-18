"""
due_post_selector.py — Deterministic due-post detection for TikTok Direct
Post publishing (Milestone 2.1.1, corrected in Milestone 2.1.2).

What it does:
  Answers exactly one question: given the database and a supplied/current
  time, which platform_posts rows are due for INITIAL execution right now?
  This is read/selection logic only — it never claims, mutates, or
  publishes anything. Ownership/claiming, worker loops, retries, and crash
  recovery are explicitly out of scope here; see
  docs/evaluations/scheduling/milestone-2.1.1-due-post-detection.md and
  docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md
  for the full validation record, and
  docs/decisions/0006-tiktok-publisher-foundation.md for the platform_posts
  schema/state machine this reads.

  Mirrors slot_matcher.py's shape deliberately: deterministic,
  content_store-dependent, one selection concern, an optional injected
  `now` for tests. Same architecture, opposite end of the pipeline —
  slot_matcher.py decides which content_slot a video goes into;
  this decides which already-scheduled platform_posts row is due to be
  worked on next.

Lifecycle and eligible statuses (corrected in Milestone 2.1.2):
  PENDING     -> eligible for initial execution (returned by this selector)
  PUBLISHING  -> already claimed/in progress (NOT returned here — a future
                 atomic-claim milestone transitions PENDING -> PUBLISHING;
                 conflating "not yet claimed" with "already claimed" in the
                 same selection would make that claim step meaningless)
  PUBLISHED   -> terminal success (not returned)
  FAILED      -> terminal failure for now (not returned) — documented in
                 publish_tiktok.py's module docstring as "left as a
                 terminal FAILED record rather than silently retried";
                 this selector treats it the same way. (Note:
                 publish_tiktok.py's own manual-rerun path does not
                 currently re-check status before re-polling an
                 already-FAILED row with a platform_post_id set — a
                 pre-existing quirk this milestone did not touch.)

  Milestone 2.1.1 originally included PUBLISHING as eligible here. Corrected
  in 2.1.2: normal due-post detection should surface only work that hasn't
  been claimed/started yet. Recovering a stale/interrupted PUBLISHING row
  belongs to a later crash-recovery milestone, not ordinary due detection —
  conflating the two here would make that future distinction impossible to
  express cleanly.

Milestone 2.1.2 closed the architectural gap Milestone 2.1.1 found: videos
now get a PENDING platform_posts row materialized as soon as they're
assigned a content_slot (see platform_post_materializer.py), not only when
publish_tiktok.py is manually run — so this selector can now discover a
scheduled delivery immediately, without requiring a prior manual publish
attempt.

Milestone 2.1.6 (retry classification and backoff): a PENDING row waiting
out a scheduled retry (platform_posts.next_retry_at set, in the future) is
not due yet even though it's PENDING — content_store.get_due_platform_posts
requires next_retry_at IS NULL OR next_retry_at <= now, using the same
now_iso/naive-local-time convention as scheduled_at. See
docs/evaluations/scheduling/milestone-2.1.6-retry-backoff.md.

Milestone 2.1.7 (missed-schedule behavior): scheduled_at <= now above has
no upper bound, so a PENDING row does not stop being due just because it
is hours or days overdue (e.g. the worker was offline past its scheduled
time) — it is selected and published exactly like any other due row, and
scheduled_at is never rewritten to the actual execution time. See
docs/evaluations/scheduling/milestone-2.1.7-missed-schedule-behavior.md.

Dependencies:
  content_store.py, slot_matcher.now_in_config_timezone (reused, not
  duplicated — same naive-local-time convention as content_slots.scheduled_at,
  which platform_posts.scheduled_at is copied from at materialization time).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from content_automation.config import TIMEZONE
from content_automation.persistence.content_store import ContentStore, PlatformPostRecord
from content_automation.scheduling.slot_matcher import now_in_config_timezone

ELIGIBLE_STATUSES = ["PENDING"]


def get_due_posts(store: ContentStore, platform: str, now: datetime | None = None) -> list[PlatformPostRecord]:
    """Due platform_posts rows for `platform`: scheduled_at is set and at
    or before `now` (inclusive — a post scheduled exactly at `now` is
    due), status is still PENDING (never yet claimed/submitted — see
    module docstring for why PUBLISHING is deliberately excluded).
    Returned earliest-scheduled first, ties broken by id, for
    deterministic ordering. Pure read; never mutates a row.

    `now` defaults to the current time in config.TIMEZONE, naive (no
    tzinfo) — matching how scheduled_at is stored (see
    slot_matcher.now_in_config_timezone). Pass a fixed naive datetime in
    tests rather than relying on wall-clock time.
    """
    resolved_now = now if now is not None else now_in_config_timezone()
    return store.get_due_platform_posts(platform, resolved_now.isoformat(), ELIGIBLE_STATUSES)


def calculate_schedule_delay(scheduled_at: str, published_at: str) -> timedelta:
    """How much later than its original scheduled_at a platform_posts row
    actually published (Milestone 2.1.7), e.g.
    calculate_schedule_delay(record.scheduled_at, record.published_at).
    Pure derivation, not a persisted field — lateness is never written to
    the database, only computed on demand from the two timestamps that are
    already stored. A non-positive result means the row published at or
    before its schedule, which the normal pipeline always produces for an
    on-time post.

    scheduled_at is a naive local-time isoformat string (config.TIMEZONE,
    same convention as this module's `now` — see module docstring), but
    published_at is written as an aware UTC isoformat string (publish_tiktok
    and crash_recovery's `_now_iso()`, the same convention as updated_at) —
    a genuine pre-existing mismatch between the two columns' conventions,
    not something this milestone introduces. Subtracting them directly
    would either raise (naive vs. aware) or, if blindly stripped of tzinfo,
    silently misreport lateness by config.TIMEZONE's UTC offset. Converting
    published_at into config.TIMEZONE before subtracting is therefore not
    optional formatting — it is required for the result to be correct."""
    published_local = datetime.fromisoformat(published_at).astimezone(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    return published_local - datetime.fromisoformat(scheduled_at)
