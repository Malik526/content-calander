"""
backfill_platform_posts.py — One-time backfill: materialize PENDING
platform_posts rows for videos that were already assigned a content_slot
before Milestone 2.1.2 introduced automatic materialization at assignment
time (see platform_post_materializer.py and
docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md).

What it does:
  For every video with an assigned content_slot that doesn't yet have a
  platform_posts row for a platform in config.TARGET_PUBLISHING_PLATFORMS,
  creates one PENDING row with scheduled_at copied from that slot — via
  the exact same idempotent primitive
  (ContentStore.insert_platform_post_if_missing) platform_post_materializer.py
  uses going forward, so this script and ordinary new assignment can never
  disagree about materialization semantics, and this script is always
  safe to re-run (a video that already has a row for a platform is left
  completely untouched, regardless of that row's status).

  Purely additive: never updates an existing platform_posts row of any
  status. Necessary because Milestone 2.1.2's automatic materialization
  only runs going forward, at new assignment time (process_content.py) —
  it does not retroactively touch videos assigned before this milestone.

Run:
  python3 backfill_platform_posts.py
  python3 backfill_platform_posts.py --dry-run
"""

import argparse
from datetime import datetime, timezone

from content_automation.persistence.content_store import ContentStore
from content_automation.scheduling import platform_post_materializer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def backfill_missing_platform_posts(store: ContentStore, *, dry_run: bool = False) -> list[dict]:
    """Returns one report dict per (video, platform) pair that needed a new
    row: {"video_id", "platform", "scheduled_at"}. Applies immediately
    unless dry_run. Never touches a (video, platform) pair that already
    has a platform_posts row."""
    report = []
    rows = store._conn.execute(
        "SELECT id AS video_id, assigned_slot_id AS slot_id FROM videos "
        "WHERE assigned_slot_id IS NOT NULL ORDER BY id"
    ).fetchall()

    for row in rows:
        video_id, slot_id = row["video_id"], row["slot_id"]
        slot = store.get_slot(slot_id)
        if slot is None:
            continue  # dangling reference — not this script's concern to repair
        for platform in platform_post_materializer.TARGET_PUBLISHING_PLATFORMS:
            if store.get_platform_post(video_id, platform) is not None:
                continue
            report.append({"video_id": video_id, "platform": platform, "scheduled_at": slot.scheduled_at})
            if not dry_run:
                store.insert_platform_post_if_missing(
                    video_id, platform, scheduled_at=slot.scheduled_at, created_at=_now_iso()
                )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time backfill: materialize missing PENDING platform_posts rows for videos "
        "already assigned a content_slot before Milestone 2.1.2. Never touches an existing row."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would be created without writing it.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with ContentStore() as store:
        report = backfill_missing_platform_posts(store, dry_run=args.dry_run)

    if not report:
        print("No missing platform_posts rows found — nothing to backfill.")
        return

    verb = "Would create" if args.dry_run else "Created"
    print(f"{verb} {len(report)} platform_posts row(s):")
    for entry in report:
        print(f"  video {entry['video_id']}: platform={entry['platform']} scheduled_at={entry['scheduled_at']}")

    if args.dry_run:
        print("\nDry run: no changes written.")


if __name__ == "__main__":
    main()
