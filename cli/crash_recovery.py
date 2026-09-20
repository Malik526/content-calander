"""
cli/crash_recovery.py — Thin CLI entry point for one-pass crash recovery
of stale PUBLISHING platform_posts rows (Milestone 2.1.5; thinned in
Milestone 3.0's package refactor).

All actual logic lives in
content_automation.scheduling.crash_recovery.recover_stale_posts_once —
this file only parses arguments, constructs the real TikTokPublisher, and
prints the resulting summary.

Run:
  python3 cli/crash_recovery.py
  python3 cli/crash_recovery.py --platform tiktok
"""

import argparse

from content_automation.persistence.content_store import ContentStore
from content_automation.scheduling.crash_recovery import recover_stale_posts_once


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one pass: find stale PUBLISHING platform_posts rows and recover them — requeue "
        "unsubmitted claims, poll submitted ones. Not a loop/daemon — exits after this one pass."
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
        summary = recover_stale_posts_once(store, publisher, platform=args.platform, user_id=user.id)

    print(
        f"discovered={summary.discovered} requeued={summary.requeued} polled={summary.polled} "
        f"published={summary.published} failed={summary.failed} still_processing={summary.still_processing}"
    )
    for err in summary.errors:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
