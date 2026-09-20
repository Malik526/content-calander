"""
cli/reconciliation.py — Thin CLI entry point for one-pass publish-status
reconciliation (Milestone 2.1.10; thinned in Milestone 3.0's package
refactor).

All actual logic lives in
content_automation.scheduling.reconciliation.reconcile_pending_status_checks_once
— this file only parses arguments, constructs the real TikTokPublisher,
and prints the resulting summary.

Run:
  python3 cli/reconciliation.py
  python3 cli/reconciliation.py --platform tiktok
"""

import argparse

from content_automation.persistence.content_store import ContentStore
from content_automation.scheduling.reconciliation import reconcile_pending_status_checks_once


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one pass: re-check TikTok's status for every PUBLISHING platform_posts row that "
        "already has a platform_post_id and is due for another check. Never resubmits. Not a loop/daemon — "
        "exits after this one pass."
    )
    parser.add_argument("--platform", default="tiktok", help="Platform to process (default: tiktok).")
    return parser.parse_args()


def main() -> None:
    from content_automation.publishing.tiktok.publisher import TikTokPublisher

    args = parse_args()
    publisher = TikTokPublisher()

    with ContentStore() as store:
        # Milestone 3.2 (ownership): see cli/worker.py's matching comment.
        user = store.get_or_create_local_user()
        summary = reconcile_pending_status_checks_once(store, publisher, platform=args.platform, user_id=user.id)

    print(
        f"discovered={summary.discovered} published={summary.published} failed={summary.failed} "
        f"still_processing={summary.still_processing}"
    )
    for err in summary.errors:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
