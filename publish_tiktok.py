"""
publish_tiktok.py — Standalone manual CLI: publish one already-processed
video to TikTok. Also the shared TikTok execution path used by worker.py's
one-pass worker (Milestone 2.1.4) — both drive execute_claimed_platform_post()
so there is exactly one real implementation of the publish flow.

What it does:
  python3 publish_tiktok.py --video-id <id>

  load video from SQLite -> resolve its local processed MP4 -> load stored
  caption -> verify media/file exists -> query TikTok creator/account
  capabilities -> initialize upload -> upload local MP4 -> publish
  privately -> obtain publish ID -> poll/check publish status -> persist
  outcome in platform_posts.

  Not wired into process_content.py — that only assigns a slot and
  materializes a PENDING platform_posts row (platform_post_materializer.py,
  Milestone 2.1.2). Actual publishing happens here, invoked either
  manually (this CLI) or by worker.py's one-pass worker.

Ownership (Milestone 2.1.4 — reconciled; previously a plain unconditional
write, see docs/evaluations/scheduling/milestone-2.1.3-atomic-platform-post-claiming.md
"Publisher Compatibility Finding"):
  The one real PENDING -> PUBLISHING mechanism is
  content_store.ContentStore.claim_platform_post() — an atomic conditional
  UPDATE. publish_video() (this CLI) now claims through it exactly like
  worker.py does, instead of writing status="PUBLISHING" directly. A
  FAILED row that never obtained a platform_post_id (a true submission
  failure — see Idempotency below) is requeued to PENDING first so it can
  be claimed again through the same mechanism, preserving the existing
  manual-retry behavior without a second ownership path.

Idempotency:
  Exactly one platform_posts row exists per (video, platform) — enforced by
  a UNIQUE constraint, not just caller discipline (content_store.py). Once
  a real TikTok publish_id has been obtained for a video, this script never
  submits it again: it only re-polls that existing submission's status,
  whether the fetch of it errors, is still processing, or is already
  terminal. Only a video that has NEVER obtained a publish_id (no
  platform_posts row, or one still PENDING/requeued-from-FAILED with
  platform_post_id=NULL — a true submission failure: local file missing,
  auth error, network error, or an upload rejected before TikTok ever
  returned an id) is eligible to (re)submit, and only via a successful
  claim_platform_post() — this distinguishes "submission never succeeded"
  from "submission succeeded but post-processing/status came back FAILED",
  which is left as a terminal FAILED record rather than silently retried.

Run:
  python3 publish_tiktok.py --video-id 3
  python3 publish_tiktok.py --video-id 3 --privacy-level SELF_ONLY
  python3 publish_tiktok.py --video-id 3 --poll-only   # re-check an existing in-flight submission only

Dependencies:
  content_store.py, publisher.py, tiktok_publisher.py, media.py, config.py
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import media
from content_store import ContentStore, VideoRecord
from publisher import PublishError, Publisher
from tiktok_publisher import TikTokPublisher


class PublishTikTokError(Exception):
    """User-facing failure — caught by main() and reported with exit(1)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slot_scheduled_at(store: ContentStore, video: VideoRecord) -> str | None:
    if video.assigned_slot_id is None:
        return None
    slot = store.get_slot(video.assigned_slot_id)
    return slot.scheduled_at if slot else None


def _validate_ready_to_publish(video: VideoRecord) -> None:
    if not video.canonical_media_path or not Path(video.canonical_media_path).exists():
        raise PublishTikTokError(
            f"Local media file for video {video.id} not found ({video.canonical_media_path!r})."
        )
    if not video.caption_text:
        raise PublishTikTokError(f"Video {video.id} has no stored caption_text — cannot publish without one.")

    info = media.MediaInfo(
        path=Path(video.canonical_media_path), container=video.container, video_codec=video.video_codec,
        audio_codec=video.audio_codec, width=video.width, height=video.height, fps=video.fps,
        duration_seconds=video.duration_seconds, file_size_bytes=video.file_size_bytes,
    )
    compatible, reason = media.is_tiktok_compatible(info)
    if not compatible:
        raise PublishTikTokError(f"Video {video.id} is not TikTok-compatible: {reason}")


def _poll_and_update(store: ContentStore, record, publisher: Publisher) -> None:
    """Check an existing submission's status and persist the result.
    Never resubmits — only ever reads/updates the record it's given."""
    try:
        status_result = publisher.get_status(record.platform_post_id)
    except PublishError as exc:
        print(f"WARNING: could not fetch publish status: {exc}", file=sys.stderr)
        return

    print(f"TikTok status: {status_result.status}")
    if status_result.status == "PUBLISH_COMPLETE":
        store.update_platform_post(record.id, updated_at=_now_iso(), status="PUBLISHED", published_at=_now_iso())
        print(f"Published. platform_post_id={record.platform_post_id}")
    elif status_result.status == "FAILED":
        store.update_platform_post(
            record.id, updated_at=_now_iso(), status="FAILED", failure_reason=status_result.failure_reason
        )
        print(f"TikTok reported failure: {status_result.failure_reason}")
    else:
        print("Not yet final — rerun with --poll-only to check again.")


def execute_claimed_platform_post(store: ContentStore, video_id: int, platform: str, publisher: Publisher) -> None:
    """Execute the proven TikTok publish flow for a platform_posts row that
    has ALREADY been claimed (status == PUBLISHING) via
    ContentStore.claim_platform_post(). Does not claim, and does not
    require or re-check PENDING — ownership must already be established by
    the caller before this is invoked.

    Shared by publish_video() (this file's manual CLI, after it claims)
    and worker.py's one-pass worker (Milestone 2.1.4), so both drive
    exactly the same publish path instead of duplicating TikTok publishing
    logic. See module docstring for the ownership reconciliation.

    Validates the video is actually publishable before calling the
    publisher (belt-and-suspenders: publish_video() already validates
    before ever inserting/claiming a row for a brand-new video, but
    worker.py claims a pre-existing row with no equivalent earlier
    checkpoint, so this is the one place that check is guaranteed to run
    for every caller).
    """
    video = store.get_video(video_id)
    if video is None:
        raise PublishTikTokError(f"No video with id={video_id}.")
    record = store.get_platform_post(video_id, platform)
    if record is None:
        raise PublishTikTokError(f"No platform_posts row for video={video_id} platform={platform!r}.")

    _validate_ready_to_publish(video)

    try:
        result = publisher.publish(Path(video.canonical_media_path), video.caption_text)
    except PublishError as exc:
        store.update_platform_post(record.id, updated_at=_now_iso(), status="FAILED", failure_reason=str(exc))
        raise PublishTikTokError(f"Submission failed: {exc}") from exc

    # Persist the publish_id immediately, separately from the eventual
    # status outcome — this is what makes a crash between submission and
    # polling safe: the next run sees platform_post_id set and only polls,
    # never resubmits.
    store.update_platform_post(
        record.id, updated_at=_now_iso(), status="PUBLISHING", platform_post_id=result.platform_post_id
    )
    print(f"Submitted to TikTok: publish_id={result.platform_post_id}")

    record = store.get_platform_post(video_id, platform)
    _poll_and_update(store, record, publisher)


def publish_video(store: ContentStore, video_id: int, publisher: Publisher, *, poll_only: bool = False) -> None:
    video = store.get_video(video_id)
    if video is None:
        raise PublishTikTokError(f"No video with id={video_id}.")

    record = store.get_platform_post(video_id, "tiktok")

    # A publish_id already exists: TikTok has already accepted a submission
    # for this video. Never submit a second one — only re-check status,
    # regardless of whether it's still processing or already terminal.
    if record is not None and record.platform_post_id:
        if record.status == "PUBLISHED":
            print(f"Video {video_id} is already PUBLISHED as TikTok post {record.platform_post_id}.")
            return
        _poll_and_update(store, record, publisher)
        return

    if poll_only:
        raise PublishTikTokError(f"No in-flight TikTok submission for video {video_id} to poll.")

    if record is None:
        # Brand-new video: validate before ever creating a row, so a
        # precondition failure (missing file/caption) leaves nothing to
        # clean up — matches Milestone 2.0's original guarantee.
        _validate_ready_to_publish(video)
        record = store.insert_platform_post(
            video_id, "tiktok", created_at=_now_iso(), scheduled_at=_slot_scheduled_at(store, video)
        )
    elif record.status == "FAILED":
        # A true submission failure (no platform_post_id was ever obtained
        # — a row WITH one already returned above) is a legitimate manual
        # retry, not a duplicate. Requeue to PENDING so
        # claim_platform_post() — the one real PENDING->PUBLISHING
        # ownership mechanism — can claim it like any other due work,
        # instead of writing PUBLISHING directly.
        store.update_platform_post(record.id, updated_at=_now_iso(), status="PENDING")

    claimed = store.claim_platform_post(record.id, updated_at=_now_iso())
    if not claimed:
        current = store.get_platform_post(video_id, "tiktok")
        raise PublishTikTokError(
            f"Video {video_id}'s TikTok post could not be claimed for submission "
            f"(current status: {current.status if current else 'unknown'} — "
            "likely already claimed by another process)."
        )

    execute_claimed_platform_post(store, video_id, "tiktok", publisher)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
