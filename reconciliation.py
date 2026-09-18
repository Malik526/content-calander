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

Run:
  python3 reconciliation.py
  python3 reconciliation.py --platform tiktok

Dependencies:
  content_store.py, publish_tiktok.py (_resolve_poll_outcome,
  _next_status_check_at), publisher.py, config.py.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from content_store import ContentStore
from publish_tiktok import _next_status_check_at, _resolve_poll_outcome
from publisher import PublishError, Publisher


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

    A transient failure to even reach the status endpoint (PublishError)
    also reschedules the next check with the same backoff, rather than
    leaving the row stuck at its old (now-elapsed) next_status_check_at
    forever — never a resubmission either way.

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
            store.update_platform_post_if_unchanged(
                record.id, expected_updated_at=record.updated_at, updated_at=_now_iso(),
                next_status_check_at=_next_status_check_at(record.status_check_count, now),
                status_check_count=record.status_check_count + 1,
            )
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one pass: re-check TikTok's status for every PUBLISHING platform_posts row that "
        "already has a platform_post_id and is due for another check. Never resubmits. Not a loop/daemon — "
        "exits after this one pass."
    )
    parser.add_argument("--platform", default="tiktok", help="Platform to process (default: tiktok).")
    return parser.parse_args()


def main() -> None:
    from tiktok_publisher import TikTokPublisher

    args = parse_args()
    publisher = TikTokPublisher()

    with ContentStore() as store:
        summary = reconcile_pending_status_checks_once(store, publisher, platform=args.platform)

    print(
        f"discovered={summary.discovered} published={summary.published} failed={summary.failed} "
        f"still_processing={summary.still_processing}"
    )
    for err in summary.errors:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
