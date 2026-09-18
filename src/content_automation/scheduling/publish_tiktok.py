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

Run (Milestone 3.0: thin CLI entry point at cli/publish_tiktok.py):
  python3 cli/publish_tiktok.py --video-id 3
  python3 cli/publish_tiktok.py --video-id 3 --privacy-level SELF_ONLY
  python3 cli/publish_tiktok.py --video-id 3 --poll-only   # re-check an existing in-flight submission only

  publish_video() below is plain parameter-driven logic (store, video_id,
  publisher) with no argparse coupling — callable directly (e.g. a future
  FastAPI endpoint, Milestone 3.1+) without going through the CLI at all.

Dependencies:
  content_automation.persistence.content_store,
  content_automation.publishing.publisher,
  content_automation.publishing.tiktok.publisher,
  content_automation.media.inspection, config.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from content_automation.config import MAX_RETRY_ATTEMPTS, RETRY_BACKOFF_MINUTES, STATUS_CHECK_BACKOFF_SECONDS
from content_automation.media import inspection as media
from content_automation.persistence.content_store import ContentStore, PlatformPostRecord, VideoRecord
from content_automation.publishing.publisher import PublishError, Publisher, PublishStatusResult
from content_automation.scheduling import retry_classification
from content_automation.scheduling.slot_matcher import now_in_config_timezone


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


def _schedule_retry_or_fail(store: ContentStore, record: PlatformPostRecord, error: PublishError) -> None:
    """A PublishError occurred before a platform_post_id was ever obtained
    for `record` (the platform_post_id rule stays absolute — see module
    docstring — this is only ever called pre-submission). Classify it
    (retry_classification.py) against the existing retry budget:

    - retryable AND retry_count < config.MAX_RETRY_ATTEMPTS: back to
      PENDING, retry_count incremented, next_retry_at set
      config.RETRY_BACKOFF_MINUTES[retry_count] minutes out (naive local
      time — the same convention scheduled_at uses, so
      due_post_selector's single `now` compares against both). Not
      claimed again here — the normal claim_platform_post() path picks it
      up once next_retry_at arrives, exactly like any other due work.
    - otherwise (terminal, or retries exhausted): FAILED. Retry
      exhaustion preserves this final failure_reason so a future UI can
      explain why the post stopped retrying.

    Either way failure_reason is set to str(error) — never silently
    dropped, whether this is attempt 1 or the final one.
    """
    if retry_classification.classify(error) and record.retry_count < MAX_RETRY_ATTEMPTS:
        delay_minutes = RETRY_BACKOFF_MINUTES[record.retry_count]
        next_retry_at = (now_in_config_timezone() + timedelta(minutes=delay_minutes)).isoformat()
        store.update_platform_post(
            record.id, updated_at=_now_iso(), status="PENDING",
            retry_count=record.retry_count + 1, next_retry_at=next_retry_at,
            failure_reason=str(error),
        )
        print(f"Retryable failure ({error.reason_code}) — retry {record.retry_count + 1}/{MAX_RETRY_ATTEMPTS} at {next_retry_at}.")
    else:
        store.update_platform_post(record.id, updated_at=_now_iso(), status="FAILED", failure_reason=str(error))


def _next_status_check_at(status_check_count: int, now: datetime) -> str:
    """Aware-UTC isoformat timestamp for the next automatic reconciliation
    check (Milestone 2.1.10), indexed by how many status checks a row has
    already had — capped at config.STATUS_CHECK_BACKOFF_SECONDS' last
    (longest) interval rather than growing unbounded or ever exhausting
    (there is no retry-budget equivalent here; nothing was ever
    resubmitted to "use up" — TikTok will eventually reach a terminal
    status). `now` must be aware UTC, matching next_status_check_at's own
    storage convention (see content_store.py's migration comment)."""
    index = min(status_check_count, len(STATUS_CHECK_BACKOFF_SECONDS) - 1)
    return (now + timedelta(seconds=STATUS_CHECK_BACKOFF_SECONDS[index])).isoformat()


def _resolve_poll_outcome(status_result: PublishStatusResult) -> tuple[str, dict]:
    """The one TikTok-status -> platform_posts-fields mapping, shared
    (Milestone 2.1.10) by every caller that ever checks an existing
    submission's status: _poll_and_update below (the synchronous poll
    right after submission, and the manual --poll-only CLI),
    crash_recovery.py's Case B (stale-PUBLISHING safety net), and
    reconciliation.py (the routine automatic re-check). Never two
    independently-maintained copies of this decision.

    Returns (outcome, fields): outcome is one of "PUBLISHED"/"FAILED"/
    "PROCESSING"; fields is what to persist for a terminal outcome
    (excluding updated_at, which every caller already supplies itself) —
    empty for "PROCESSING", since what to persist there (next_status_check_at/
    status_check_count) depends on each caller's own scheduling state, not
    on the status result alone."""
    if status_result.status == "PUBLISH_COMPLETE":
        return "PUBLISHED", {"status": "PUBLISHED", "published_at": _now_iso()}
    if status_result.status == "FAILED":
        return "FAILED", {"status": "FAILED", "failure_reason": status_result.failure_reason}
    return "PROCESSING", {}


def _poll_and_update(store: ContentStore, record, publisher: Publisher) -> None:
    """Check an existing submission's status and persist the result.
    Never resubmits — only ever reads/updates the record it's given.

    Milestone 2.1.10: a still-processing outcome now also schedules the
    first automatic reconciliation check (next_status_check_at/
    status_check_count) instead of leaving the row to wait on a human
    rerunning --poll-only — reconciliation.py picks it up from here."""
    try:
        status_result = publisher.get_status(record.platform_post_id)
    except PublishError as exc:
        print(f"WARNING: could not fetch publish status: {exc}", file=sys.stderr)
        return

    print(f"TikTok status: {status_result.status}")
    outcome, fields = _resolve_poll_outcome(status_result)

    if outcome == "PROCESSING":
        fields = {
            "next_status_check_at": _next_status_check_at(record.status_check_count, datetime.now(timezone.utc)),
            "status_check_count": record.status_check_count + 1,
        }
        store.update_platform_post(record.id, updated_at=_now_iso(), **fields)
        print("Not yet final — scheduled for automatic reconciliation (see reconciliation.py), "
              "or rerun with --poll-only to check again immediately.")
        return

    store.update_platform_post(record.id, updated_at=_now_iso(), **fields)
    if outcome == "PUBLISHED":
        print(f"Published. platform_post_id={record.platform_post_id}")
    else:
        print(f"TikTok reported failure: {status_result.failure_reason}")


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

    Milestone 2.1.6 fix: a precondition failure here (missing file,
    missing caption, incompatible container/codec) used to propagate
    uncaught, leaving an already-claimed row stuck PUBLISHING forever with
    no failure_reason — crash_recovery.py would eventually requeue it
    (platform_post_id is still NULL), it would get re-claimed, fail the
    same validation again, and repeat indefinitely for a video whose
    problem never resolves on its own. Local validation failures are
    unconditionally terminal (see retry_classification.py's module
    docstring for why they're not routed through classification at all) —
    now caught here and marked FAILED immediately, same as any other
    terminal publishing failure.
    """
    video = store.get_video(video_id)
    if video is None:
        raise PublishTikTokError(f"No video with id={video_id}.")
    record = store.get_platform_post(video_id, platform)
    if record is None:
        raise PublishTikTokError(f"No platform_posts row for video={video_id} platform={platform!r}.")

    try:
        _validate_ready_to_publish(video)
    except PublishTikTokError as exc:
        store.update_platform_post(record.id, updated_at=_now_iso(), status="FAILED", failure_reason=str(exc))
        raise

    try:
        result = publisher.publish(Path(video.canonical_media_path), video.caption_text)
    except PublishError as exc:
        _schedule_retry_or_fail(store, record, exc)
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
        # instead of writing PUBLISHING directly. A human explicitly
        # rerunning this CLI is a fresh attempt, independent of the
        # automatic retry/backoff budget (Milestone 2.1.6) — reset it
        # rather than inheriting whatever retry_count the automatic path
        # had already accumulated.
        store.update_platform_post(
            record.id, updated_at=_now_iso(), status="PENDING", retry_count=0, next_retry_at=None
        )

    claimed = store.claim_platform_post(record.id, updated_at=_now_iso())
    if not claimed:
        current = store.get_platform_post(video_id, "tiktok")
        raise PublishTikTokError(
            f"Video {video_id}'s TikTok post could not be claimed for submission "
            f"(current status: {current.status if current else 'unknown'} — "
            "likely already claimed by another process)."
        )

    execute_claimed_platform_post(store, video_id, "tiktok", publisher)
