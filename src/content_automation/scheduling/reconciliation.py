"""
reconciliation.py — One-pass automatic status reconciliation for
PUBLISHING platform_posts rows TikTok has already accepted (Milestone
2.1.10).

What it does:
  Closes the gap Milestone 2.1.9's live validation exposed: a real TikTok
  submission can still be PROCESSING_UPLOAD after the worker's one inline
  poll (publish_tiktok._poll_and_update, run immediately after
  submission), and until this milestone nothing automatically checked it
  again — a human had to rerun `publish_tiktok.py --video-id N
  --poll-only`. This module removes that human step with routine polling,
  not webhooks (out of scope until Milestone 3's hosted productization —
  see docs/evaluations/scheduling/milestone-2.1.10-asynchronous-publish-reconciliation.md).

  Eligible rows: status = PUBLISHING AND platform_post_id IS NOT NULL AND
  due for another check (content_store.get_reconcilable_platform_posts).
  Deliberately separate from due_post_selector.py (PENDING-only — work
  never yet submitted) and crash_recovery.py (staleness-gated safety net
  for an abandoned/crashed claim, which doesn't require a platform_post_id
  at all). The three selectors never overlap in practice: a row here
  always HAS a platform_post_id, so it can never also be a Case-A crash-
  recovery candidate, and get_due_platform_posts only ever returns
  PENDING rows.

  Reconciliation is status-only, forever, once platform_post_id exists —
  the same unconditional invariant crash_recovery.py and publish_tiktok.py
  already enforce. This module never calls publisher.publish(), never
  inits an upload, never creates a platform_posts row.

  Every status-check caller (this module, crash_recovery.py's Case B, and
  publish_tiktok._poll_and_update) shares one mapping
  (publish_tiktok._resolve_poll_outcome) from a TikTok status to the
  platform_posts fields it implies — never independently duplicated.

  Backoff: config.STATUS_CHECK_BACKOFF_SECONDS (default 30s/60s/2m/5m/10m),
  indexed by platform_posts.status_check_count and capped at the last
  interval — no tight loop, no unbounded growth, no permanent daemon. Every
  write uses ContentStore.update_platform_post_if_unchanged (optimistic
  concurrency), the same primitive crash_recovery.py already established,
  so two concurrent reconciliation passes — or a reconciliation pass
  racing a crash-recovery pass touching the same row — can't corrupt each
  other's update; the loser's write simply no-ops.

  One pass only: no daemon, no cron, no busy loop. Intended to run once
  per scheduler/cron invocation, alongside (before, per this module's own
  recommendation) worker.py's due-post pass — see main() below and
  docs/evaluations/scheduling/milestone-2.1.10-asynchronous-publish-reconciliation.md
  "Worker Integration".

Run (Milestone 3.0: thin CLI entry point at cli/reconciliation.py):
  python3 cli/reconciliation.py
  python3 cli/reconciliation.py --platform tiktok

Dependencies:
  content_automation.persistence.content_store,
  content_automation.scheduling.publish_tiktok (_resolve_poll_outcome,
  _next_status_check_at), content_automation.publishing.publisher, config.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, Publisher
from content_automation.scheduling import retry_classification
from content_automation.scheduling.publish_tiktok import _next_status_check_at, _resolve_poll_outcome


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconciliationSummary:
    discovered: int = 0
    published: int = 0
    failed: int = 0
    still_processing: int = 0
    errors: list = field(default_factory=list)


def reconcile_pending_status_checks_once(
    store: ContentStore, publisher: Publisher, *, platform: str = "tiktok", now: datetime | None = None,
) -> ReconciliationSummary:
    """Find PUBLISHING platform_posts rows for `platform` with a
    platform_post_id that are due for another status check, and check
    each one exactly once: publisher.get_status() (never publish()),
    mapped through publish_tiktok._resolve_poll_outcome, and persisted via
    optimistic-concurrency update. A still-processing outcome reschedules
    the next check (config.STATUS_CHECK_BACKOFF_SECONDS, indexed by
    status_check_count); a terminal outcome (PUBLISHED/FAILED) needs no
    further scheduling — the row leaves PUBLISHING entirely, so
    get_reconcilable_platform_posts will never select it again regardless.

    A failure to even reach/use the status endpoint (PublishError, most
    commonly from the token refresh path inside publisher.get_status() —
    Milestone 2.1.8) is classified exactly like a publishing failure
    already is (retry_classification.is_retryable, the same reason_code/
    http_status-driven decision publish_tiktok._schedule_retry_or_fail
    already uses — not a second independently-invented rule):

      - retryable (e.g. a transient network blip, a temporary 5xx from
        TikTok's token endpoint): the row stays PUBLISHING and the next
        check is rescheduled with the same backoff, rather than leaving
        it stuck at its old (now-elapsed) next_status_check_at forever.
      - terminal (TikTokReauthorizationRequiredError's
        REAUTHORIZATION_REQUIRED — the refresh token is expired, revoked,
        or was never issued): the row is marked FAILED immediately with
        the actionable reconnect message as failure_reason, and no
        further check is scheduled. A row that genuinely needs a human to
        re-run `tiktok_auth.py --authorize` must not sit PUBLISHING and
        get silently re-polled forever — nothing about waiting longer
        ever resolves it, exactly the same reasoning
        retry_classification.py already applies to a publishing attempt
        that hits this error. No new lifecycle status was introduced:
        this is the same FAILED every other terminal outcome already
        uses.

    Neither branch ever resubmits — get_status() is the only TikTok call
    reconciliation ever makes.

    `now` is forwarded to content_store.get_reconcilable_platform_posts
    for deterministic testing (see that method's docstring for the
    aware-UTC convention this module uses, distinct from
    due_post_selector's naive-local-time `now`).
    """
    now = now if now is not None else datetime.now(timezone.utc)
    summary = ReconciliationSummary()

    rows = store.get_reconcilable_platform_posts(platform, now.isoformat())
    summary.discovered = len(rows)

    for record in rows:
        try:
            status_result = publisher.get_status(record.platform_post_id)
        except PublishError as exc:
            if retry_classification.is_retryable(exc.reason_code, getattr(exc, "http_status", None)):
                store.update_platform_post_if_unchanged(
                    record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(),
                    next_status_check_at=_next_status_check_at(record.status_check_count, now),
                    status_check_count=record.status_check_count + 1,
                )
            else:
                # Terminal — most notably REAUTHORIZATION_REQUIRED. Stop
                # polling: mark FAILED with the actionable message, never
                # schedule another check. No new lifecycle status.
                updated = store.update_platform_post_if_unchanged(
                    record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(),
                    status="FAILED", failure_reason=str(exc),
                )
                if updated:
                    summary.failed += 1
            summary.errors.append(str(exc))
            continue

        outcome, fields = _resolve_poll_outcome(status_result)

        if outcome == "PROCESSING":
            fields = {
                "next_status_check_at": _next_status_check_at(record.status_check_count, now),
                "status_check_count": record.status_check_count + 1,
            }
            updated = store.update_platform_post_if_unchanged(
                record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(), **fields
            )
            if updated:
                summary.still_processing += 1
            continue

        updated = store.update_platform_post_if_unchanged(
            record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(), **fields
        )
        if updated:
            if outcome == "PUBLISHED":
                summary.published += 1
            else:
                summary.failed += 1

    return summary
