"""
cli/publish_tiktok.py — Thin CLI entry point for manually publishing one
already-processed video to TikTok (Milestone 3.0's package refactor
thinned this from the original standalone publish_tiktok.py).

All actual logic (publish_video, execute_claimed_platform_post, and
everything they call) lives in
content_automation.scheduling.publish_tiktok — this file only parses
arguments, constructs the real TikTokPublisher, and reports the result.

Run:
  python3 cli/publish_tiktok.py --video-id 3
  python3 cli/publish_tiktok.py --video-id 3 --privacy-level SELF_ONLY
  python3 cli/publish_tiktok.py --video-id 3 --poll-only   # re-check an existing in-flight submission only
"""

import argparse
import sys

from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.tiktok.publisher import TikTokPublisher
from content_automation.scheduling.publish_tiktok import PublishTikTokError, publish_video


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish one already-processed video to TikTok (manual, one video at a time)."
    )
    parser.add_argument("--video-id", type=int, required=True, help="videos.id to publish.")
    parser.add_argument(
        "--privacy-level", default=None,
        help="Override config.TIKTOK_DEFAULT_PRIVACY_LEVEL (e.g. SELF_ONLY) for this run.",
    )
    parser.add_argument(
        "--poll-only", action="store_true",
        help="Only re-check an existing in-flight submission's status; never submit a new one.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    publisher_kwargs = {"privacy_level": args.privacy_level} if args.privacy_level else {}
    publisher = TikTokPublisher(**publisher_kwargs)

    try:
        with ContentStore() as store:
            publish_video(store, args.video_id, publisher, poll_only=args.poll_only)
    except PublishTikTokError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
