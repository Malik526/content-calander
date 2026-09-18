"""
crash_recovery.py — One-pass recovery for interrupted PUBLISHING
platform_posts rows (Milestone 2.1.5).

What it does:
  Answers: "a worker died mid-job — how does this pipeline safely
  continue?" A row can be left PUBLISHING forever if the process that
  claimed it (worker.py or publish_tiktok.py, both via
  ContentStore.claim_platform_post()) crashes before reaching a terminal
  state. Ordinary due detection (due_post_selector.py) ignores PUBLISHING
  rows entirely — recovery is the only thing that ever looks at them again.

  Two distinct crash classes, handled differently:

    Case A — claimed but never submitted (platform_post_id IS NULL):
      no evidence TikTok ever accepted anything. Requeued to PENDING so
      the normal atomic-claim path (claim_platform_post) can pick it up
      again later — never republished directly here.

    Case B — submitted but unresolved (platform_post_id IS NOT NULL):
      TikTok already accepted the submission. Never resubmit the media —
      Milestone 2.0's idempotency rule holds unconditionally. Only
      publisher.get_status() (read-only) is called, and the row is
      updated to PUBLISHED/FAILED/left PUBLISHING exactly like an ordinary
      poll would.

  A row is only "stale" — eligible for recovery at all — if
  config.PLATFORM_POST_STALE_MINUTES have passed since its updated_at with
  no further activity; a row a currently-running worker legitimately owns
  is left alone. Every recovery write is an optimistic-concurrency update
  (ContentStore.update_platform_post_if_unchanged) gated on the exact
  updated_at value read during selection, so recovery can only ever act on
  a row that is still the stale record it inspected — never one an active
  worker resumed and already moved on.

  One pass only: no daemon, no cron, no retry scheduler. See
  docs/evaluations/scheduling/milestone-2.1.5-crash-recovery.md.

Run (Milestone 3.0: thin CLI entry point at cli/crash_recovery.py):
  python3 cli/crash_recovery.py
  python3 cli/crash_recovery.py --platform tiktok

Dependencies:
  content_automation.persistence.content_store,
  content_automation.publishing.publisher,
  content_automation.publishing.tiktok.publisher, config.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from content_automation.config import PLATFORM_POST_STALE_MINUTES
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, Publisher
from content_automation.scheduling.publish_tiktok import _resolve_poll_outcome


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoverySummary:
    discovered: int = 0
    requeued: int = 0
    polled: int = 0
    published: int = 0
    failed: int = 0
    still_processing: int = 0
    errors: list = field(default_factory=list)


def recover_stale_posts_once(
    store: ContentStore,
    publisher: Publisher,
    *,
    platform: str = "tiktok",
    now: datetime | None = None,
    stale_after_minutes: int | None = None,
) -> RecoverySummary:
    """Find PUBLISHING platform_posts rows for `platform` that have had no
    activity for at least stale_after_minutes (default:
    config.PLATFORM_POST_STALE_MINUTES), and recover each one: requeue to
    PENDING if no platform_post_id was ever obtained (Case A), or poll
    TikTok's existing status if one was (Case B) — never resubmitting
    media either way. One pass — never loops, never waits.

    `now` must be an aware UTC datetime if supplied (matching how
    updated_at is always written) — pass a fixed value in tests rather
    than relying on wall-clock time.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    threshold_minutes = (
        stale_after_minutes if stale_after_minutes is not None else PLATFORM_POST_STALE_MINUTES
    )
    stale_before_iso = (now - timedelta(minutes=threshold_minutes)).isoformat()

    summary = RecoverySummary()
    stale_rows = store.get_recoverable_platform_posts(platform, stale_before_iso)
    summary.discovered = len(stale_rows)

    for record in stale_rows:
        if record.platform_post_id is None:
            # Case A: no evidence of a real submission — safe to requeue.
            requeued = store.update_platform_post_if_unchanged(
                record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(), status="PENDING"
            )
            if requeued:
                summary.requeued += 1
            continue

        # Case B: TikTok already has this submission — never call
        # publisher.publish() again, only check its status.
        summary.polled += 1
        try:
            status_result = publisher.get_status(record.platform_post_id)
        except PublishError as exc:
            summary.errors.append(str(exc))
            continue

        # Milestone 2.1.10: the actual status->fields decision is shared
        # with publish_tiktok.py's inline poll and reconciliation.py's
        # routine checks (publish_tiktok._resolve_poll_outcome) — never a
        # second independently-maintained copy of this mapping. Only the
        # still-processing branch stays crash-recovery-specific: it
        # deliberately does NOT write next_status_check_at/
        # status_check_count the way reconciliation.py does, for the same
        # reason it never refreshed updated_at — see the comment below.
        outcome, fields = _resolve_poll_outcome(status_result)
        if outcome == "PUBLISHED":
            updated = store.update_platform_post_if_unchanged(
                record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(), **fields
            )
            if updated:
                summary.published += 1
        elif outcome == "FAILED":
            updated = store.update_platform_post_if_unchanged(
                record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(), **fields
            )
            if updated:
                summary.failed += 1
        else:
            # Not yet final — leave the row exactly as-is (no write, same
            # as an ordinary poll's "not yet final" outcome). updated_at is
            # deliberately NOT refreshed here: doing so would reset the
            # staleness clock and could delay the next recovery pass from
            # rechecking an already-complete job for a full threshold
            # period. Leaving it untouched means the next recovery run
            # checks again immediately, which is safe — get_status() is
            # read-only and idempotent. (Milestone 2.1.10: this is
            # precisely why crash recovery stays the safety net and
            # reconciliation.py — which DOES schedule next_status_check_at
            # — is the routine path; see that module's docstring.)
            summary.still_processing += 1

    return summary
