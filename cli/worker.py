"""
cli/worker.py — Thin CLI entry point for the one-pass worker (Milestone
2.1.4; thinned in Milestone 3.0's package refactor).

All actual logic lives in content_automation.scheduling.worker
(run_due_posts_once) — this file only parses arguments, constructs the
real TikTokPublisher, and prints the resulting summary, so a future
FastAPI service (Milestone 3.1+) can call run_due_posts_once() directly
without going through this CLI at all.

Run:
  python3 cli/worker.py
  python3 cli/worker.py --platform tiktok
"""

import argparse

from content_automation.persistence.content_store import ContentStore
from content_automation.scheduling.worker import run_due_posts_once


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one pass: discover due platform_posts, atomically claim each, and execute the "
        "existing publish flow. Not a loop/daemon — exits after this one pass."
    )
    parser.add_argument("--platform", default="tiktok", help="Platform to process (default: tiktok).")
    return parser.parse_args()


def main() -> None:
    from content_automation.publishing.tiktok.publisher import TikTokPublisher

    args = parse_args()
    publisher = TikTokPublisher()

    with ContentStore() as store:
        summary = run_due_posts_once(store, publisher, platform=args.platform)

    print(
        f"discovered={summary.discovered} claimed={summary.claimed} skipped={summary.skipped} "
        f"published={summary.published} failed={summary.failed} retry_scheduled={summary.retry_scheduled}"
    )
    for err in summary.errors:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
