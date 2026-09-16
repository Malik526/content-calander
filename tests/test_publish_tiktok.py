"""Tests for publish_tiktok.py: the standalone manual publishing CLI
orchestration, with special attention to the idempotency/duplicate-
submission protection required by Milestone 2.0. A FakePublisher stands in
for TikTokPublisher throughout — no real network access or TikTok
credentials required. Uses a real (temp-file) ContentStore, since the
orchestration logic here is precisely about what content_store state
results, not just what the publisher was called with."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

import media
import publish_tiktok as pt
from content_store import ContentStore
from publisher import PublishError, PublishResult, PublishStatusResult


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def processed_video(tmp_path, store):
    """A video already through process_content.py's pipeline: validated
    media info, a transcript-derived caption, no assigned slot (kept
    simple — assignment is exercised separately)."""
    video_path = tmp_path / "processed" / "video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake mp4 bytes")

    video = store.insert_video("h1", "video.mp4", "/incoming/video.mp4", "2026-01-01T00:00:00")
    store.update_video(
        video.id,
        canonical_media_path=str(video_path),
        container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello world", caption_source="transcript_auto",
        status="ASSIGNED",
    )
    return store.get_video(video.id)


@dataclass
class FakePublisher:
    """Stands in for TikTokPublisher. Records every publish()/get_status()
    call so tests can assert idempotency (how many times each was
    actually invoked), and lets each test script exactly what should
    happen without touching any real network code."""
    publish_result: PublishResult | Exception = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult | Exception = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption))
        if isinstance(self.publish_result, Exception):
            raise self.publish_result
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        self.status_calls.append(platform_post_id)
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_publish_video_full_happy_path(store, processed_video):
    publisher = FakePublisher()

    pt.publish_video(store, processed_video.id, publisher)

    assert len(publisher.publish_calls) == 1
    assert publisher.publish_calls[0] == (Path(processed_video.canonical_media_path), "hello world")
    assert len(publisher.status_calls) == 1

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "PUBLISHED"
    assert record.platform_post_id == "pub_1"
    assert record.published_at is not None


def test_publish_video_carries_assigned_slot_scheduled_at(store, processed_video):
    store.insert_slot_if_missing("2026-09-16T09:00:00", None, None, "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot_fifo("2000-01-01T00:00:00")
    store.assign_slot(processed_video.id, slot.id)
    video = store.get_video(processed_video.id)

    pt.publish_video(store, video.id, FakePublisher())

    record = store.get_platform_post(video.id, "tiktok")
    assert record.scheduled_at == "2026-09-16T09:00:00"


def test_publish_video_with_no_assigned_slot_leaves_scheduled_at_none(store, processed_video):
    pt.publish_video(store, processed_video.id, FakePublisher())
    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.scheduled_at is None


# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------

def test_publish_video_raises_for_unknown_video_id(store):
    with pytest.raises(pt.PublishTikTokError, match="No video with id"):
        pt.publish_video(store, 999999, FakePublisher())


def test_publish_video_raises_when_missing_local_file(store):
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    store.update_video(video.id, canonical_media_path="/does/not/exist.mp4", caption_text="hi")

    with pytest.raises(pt.PublishTikTokError, match="not found"):
        pt.publish_video(store, video.id, FakePublisher())


def test_publish_video_raises_when_missing_caption(store, tmp_path):
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"data")
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    store.update_video(video.id, canonical_media_path=str(video_path), caption_text=None)

    with pytest.raises(pt.PublishTikTokError, match="caption_text"):
        pt.publish_video(store, video.id, FakePublisher())


def test_publish_video_raises_when_not_tiktok_compatible(store, tmp_path):
    video_path = tmp_path / "v.avi"
    video_path.write_bytes(b"data")
    video = store.insert_video("h1", "v.avi", "/incoming/v.avi", "2026-01-01T00:00:00")
    store.update_video(
        video.id, canonical_media_path=str(video_path), caption_text="hi",
        container="avi", video_codec="mpeg4",
    )

    with pytest.raises(pt.PublishTikTokError, match="not TikTok-compatible"):
        pt.publish_video(store, video.id, FakePublisher())


def test_precondition_failure_leaves_no_platform_post_record(store, tmp_path):
    """A precondition failure (missing file/caption) happens before a
    platform_posts row is ever created — nothing to clean up, and a later
    real attempt starts fresh."""
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    store.update_video(video.id, canonical_media_path="/does/not/exist.mp4", caption_text="hi")

    with pytest.raises(pt.PublishTikTokError):
        pt.publish_video(store, video.id, FakePublisher())

    assert store.get_platform_post(video.id, "tiktok") is None


# ---------------------------------------------------------------------------
# submission failure vs. post-processing/status failure
# ---------------------------------------------------------------------------

def test_publish_failure_is_recorded_as_failed_with_reason(store, processed_video):
    publisher = FakePublisher(publish_result=PublishError("upload rejected", reason_code="UPLOAD_FAILED"))

    with pytest.raises(pt.PublishTikTokError, match="Submission failed"):
        pt.publish_video(store, processed_video.id, publisher)

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "FAILED"
    assert "upload rejected" in record.failure_reason
    assert record.platform_post_id is None  # never obtained — a true submission failure


def test_status_poll_failure_after_successful_submission_persists_publish_id_and_stays_publishing(store, processed_video):
    """A crash/error fetching status must not lose the already-obtained
    publish_id, and must not mark the post FAILED — the submission itself
    succeeded; only the post-processing check failed."""
    publisher = FakePublisher(status_result=PublishError("status endpoint down", reason_code="NETWORK_ERROR"))

    pt.publish_video(store, processed_video.id, publisher)  # does not raise — status failure is only warned

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.platform_post_id == "pub_1"
    assert record.status == "PUBLISHING"  # not FAILED — submission succeeded, only the poll failed


def test_tiktok_reported_failure_after_submission_is_recorded_distinctly(store, processed_video):
    """TikTok accepting the submission but then reporting FAILED status is
    a post-processing failure, not a submission failure — the publish_id
    is still meaningful/persisted."""
    publisher = FakePublisher(status_result=PublishStatusResult(status="FAILED", failure_reason="video_pull_failed"))

    pt.publish_video(store, processed_video.id, publisher)

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.platform_post_id == "pub_1"  # preserved, unlike a true submission failure
    assert record.failure_reason == "video_pull_failed"


def test_still_processing_status_leaves_record_publishing(store, processed_video):
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_DOWNLOAD"))

    pt.publish_video(store, processed_video.id, publisher)

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "PUBLISHING"
    assert record.platform_post_id == "pub_1"


# ---------------------------------------------------------------------------
# idempotency / duplicate-submission protection — the core requirement
# ---------------------------------------------------------------------------

def test_republishing_an_already_published_video_does_not_resubmit(store, processed_video):
    publisher = FakePublisher()
    pt.publish_video(store, processed_video.id, publisher)
    assert len(publisher.publish_calls) == 1

    pt.publish_video(store, processed_video.id, publisher)  # second call, same video

    assert len(publisher.publish_calls) == 1  # never called again
    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "PUBLISHED"


def test_rerunning_while_still_processing_polls_instead_of_resubmitting(store, processed_video):
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_DOWNLOAD"))
    pt.publish_video(store, processed_video.id, publisher)
    assert len(publisher.publish_calls) == 1
    assert len(publisher.status_calls) == 1

    pt.publish_video(store, processed_video.id, publisher)  # rerun while still in flight

    assert len(publisher.publish_calls) == 1  # still never resubmitted
    assert len(publisher.status_calls) == 2  # but did check status again


def test_rerunning_after_tiktok_reported_failure_does_not_resubmit(store, processed_video):
    """Once a publish_id exists, even a FAILED outcome must not be
    silently retried as a brand-new post — this is the "ambiguous result"
    case the milestone explicitly calls out."""
    publisher = FakePublisher(status_result=PublishStatusResult(status="FAILED", failure_reason="video_pull_failed"))
    pt.publish_video(store, processed_video.id, publisher)
    assert len(publisher.publish_calls) == 1

    pt.publish_video(store, processed_video.id, publisher)

    assert len(publisher.publish_calls) == 1  # never resubmitted despite FAILED status
    assert len(publisher.status_calls) == 2   # still just re-polled
    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.platform_post_id == "pub_1"


def test_rerunning_after_true_submission_failure_is_allowed_to_retry(store, processed_video):
    """The opposite case: no publish_id was ever obtained, so a fresh
    attempt is a legitimate retry, not a duplicate — and it must reuse the
    existing platform_posts row (UNIQUE(video_id, platform)) rather than
    trying to insert a second one."""
    failing_publisher = FakePublisher(publish_result=PublishError("network down", reason_code="NETWORK_ERROR"))
    with pytest.raises(pt.PublishTikTokError):
        pt.publish_video(store, processed_video.id, failing_publisher)

    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.platform_post_id is None

    succeeding_publisher = FakePublisher()
    pt.publish_video(store, processed_video.id, succeeding_publisher)  # must not raise IntegrityError

    assert len(succeeding_publisher.publish_calls) == 1
    record = store.get_platform_post(processed_video.id, "tiktok")
    assert record.status == "PUBLISHED"
    assert record.platform_post_id == "pub_1"


def test_only_one_platform_post_row_ever_exists_across_multiple_runs(store, processed_video):
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_DOWNLOAD"))
    pt.publish_video(store, processed_video.id, publisher)
    pt.publish_video(store, processed_video.id, publisher)
    pt.publish_video(store, processed_video.id, publisher)

    rows = store._conn.execute(
        "SELECT COUNT(*) c FROM platform_posts WHERE video_id = ? AND platform = 'tiktok'", (processed_video.id,)
    ).fetchone()
    assert rows["c"] == 1


# ---------------------------------------------------------------------------
# --poll-only
# ---------------------------------------------------------------------------

def test_poll_only_does_not_submit_when_nothing_in_flight(store, processed_video):
    with pytest.raises(pt.PublishTikTokError, match="No in-flight"):
        pt.publish_video(store, processed_video.id, FakePublisher(), poll_only=True)


def test_poll_only_checks_status_without_submitting(store, processed_video):
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_DOWNLOAD"))
    pt.publish_video(store, processed_video.id, publisher)  # first, a real submission
    assert len(publisher.publish_calls) == 1

    pt.publish_video(store, processed_video.id, publisher, poll_only=True)

    assert len(publisher.publish_calls) == 1  # unchanged
    assert len(publisher.status_calls) == 2
