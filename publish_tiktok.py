"""
publish_tiktok.py — Standalone manual CLI: publish one already-processed
video to TikTok.

What it does:
  python3 publish_tiktok.py --video-id <id>

  load video from SQLite -> resolve its local processed MP4 -> load stored
  caption -> verify media/file exists -> query TikTok creator/account
  capabilities -> initialize upload -> upload local MP4 -> publish
  privately -> obtain publish ID -> poll/check publish status -> persist
  outcome in platform_posts.

  Deliberately NOT wired into process_content.py or any scheduled/automatic
  execution — this milestone (2.0) only proves the publish path exists and
  works for one manually-chosen video at a time. See
  docs/decisions/0006-tiktok-publisher-foundation.md.

Idempotency:
  Exactly one platform_posts row exists per (video, platform) — enforced by
  a UNIQUE constraint, not just caller discipline (content_store.py). Once
  a real TikTok publish_id has been obtained for a video, this script never
  submits it again: it only re-polls that existing submission's status,
  whether the fetch of it errors, is still processing, or is already
  terminal. Only a video that has NEVER obtained a publish_id (no
  platform_posts row, or one still PENDING with platform_post_id=NULL — a
  true submission failure: local file missing, auth error, network error,
  or an upload rejected before TikTok ever returned an id) is eligible to
  (re)submit — this distinguishes "submission never succeeded" from
  "submission succeeded but post-processing/status came back FAILED",
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

    _validate_ready_to_publish(video)

    now = _now_iso()
    if record is None:
        record = store.insert_platform_post(
            video_id, "tiktok", created_at=now, scheduled_at=_slot_scheduled_at(store, video)
        )

    store.update_platform_post(record.id, updated_at=_now_iso(), status="PUBLISHING")
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

    record = store.get_platform_post(video_id, "tiktok")
    _poll_and_update(store, record, publisher)


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
