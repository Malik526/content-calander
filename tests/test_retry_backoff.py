"""Tests for the automatic retry/backoff system (Milestone 2.1.6):
retry_classification.py's is_retryable/classify predicates wired through
publish_tiktok._schedule_retry_or_fail and worker.py's one-pass loop, plus
content_store.get_due_platform_posts' next_retry_at gating. Uses a real
(temp-file) ContentStore and FakePublisher — no real network access or
TikTok credentials required.

Distinct from tests/test_publish_tiktok.py's
test_rerunning_after_true_submission_failure_is_allowed_to_retry, which
covers the pre-existing *manual* CLI-rerun-of-a-FAILED-row path — this file
covers only the *automatic* retry_count/next_retry_at backoff system."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from content_automation.scheduling import publish_tiktok as pt
from content_automation.scheduling import retry_classification
from content_automation.scheduling import worker
from content_automation.config import MAX_RETRY_ATTEMPTS, RETRY_BACKOFF_MINUTES
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def due_video(tmp_path, store):
    """A video already processed and assigned, with a PENDING tiktok
    platform_post scheduled in the past — i.e. genuinely due right now."""
    video_path = tmp_path / "processed" / "video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake mp4 bytes")

    video = store.insert_video("h1", "video.mp4", "/incoming/video.mp4", NOW.isoformat())
    store.update_video(
        video.id,
        canonical_media_path=str(video_path),
        container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello world", caption_source="transcript_auto",
        status="ASSIGNED",
    )
    store.insert_platform_post(
        video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=(NOW - timedelta(hours=1)).isoformat()
    )
    return store.get_video(video.id)


@dataclass
class FakePublisher:
    publish_result: PublishResult | Exception = field(
        default_factory=lambda: PublishResult(platform_post_id="pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult | Exception = field(
        default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE")
    )
    publish_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption))
        if isinstance(self.publish_result, Exception):
            raise self.publish_result
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        if isinstance(self.status_result, Exception):
            raise self.status_result
        return self.status_result


# ---------------------------------------------------------------------------
# 1-4. retry_classification.is_retryable/classify: known retryable/terminal
# codes, unrecognized-code http_status fallback (5xx retryable, 4xx
# terminal), unrecognized-code-with-no-http-status defaults terminal.
# (Full parametrized coverage of the code lists already lives in
# tests/test_retry_classification.py; these four are a light smoke check
# that publish_tiktok/worker actually observe the same behavior end to end.)
# ---------------------------------------------------------------------------

def test_known_retryable_code_is_retryable():
    assert retry_classification.is_retryable("NETWORK_ERROR") is True


def test_known_terminal_code_is_not_retryable():
    assert retry_classification.is_retryable("CAPTION_TOO_LONG") is False


def test_unrecognized_code_with_5xx_status_is_retryable():
    assert retry_classification.is_retryable("SOME_NEW_TIKTOK_CODE", http_status=503) is True


def test_unrecognized_code_with_no_http_status_defaults_terminal():
    assert retry_classification.is_retryable("SOME_NEW_TIKTOK_CODE", http_status=None) is False


# ---------------------------------------------------------------------------
# 5. a retryable failure persists retry_count/next_retry_at and returns the
#    row to PENDING (not FAILED).
# ---------------------------------------------------------------------------

def test_retryable_failure_returns_row_to_pending_with_retry_state(store, due_video):
    failing = FakePublisher(publish_result=PublishError("net blip", reason_code="NETWORK_ERROR"))
    summary = worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PENDING"
    assert record.retry_count == 1
    assert record.next_retry_at is not None
    assert record.platform_post_id is None
    assert summary.retry_scheduled == 1
    assert summary.failed == 0


# ---------------------------------------------------------------------------
# 6. next_retry_at matches config.RETRY_BACKOFF_MINUTES[0] out from now.
# ---------------------------------------------------------------------------

def test_next_retry_at_matches_configured_first_backoff_delay(store, due_video):
    failing = FakePublisher(publish_result=PublishError("net blip", reason_code="NETWORK_ERROR"))
    worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    expected = NOW + timedelta(minutes=RETRY_BACKOFF_MINUTES[0])
    # next_retry_at is computed from wall-clock now_in_config_timezone(), not
    # the injected `now` — assert the delay window rather than exact equality.
    actual = datetime.fromisoformat(record.next_retry_at)
    assert abs((actual - datetime.now()).total_seconds() - RETRY_BACKOFF_MINUTES[0] * 60) < 30


# ---------------------------------------------------------------------------
# 7. a terminal failure ends FAILED immediately, with retry_count untouched.
# ---------------------------------------------------------------------------

def test_terminal_failure_ends_failed_immediately(store, due_video):
    failing = FakePublisher(publish_result=PublishError("bad caption", reason_code="CAPTION_TOO_LONG"))
    summary = worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.retry_count == 0
    assert record.next_retry_at is None
    assert record.failure_reason is not None
    assert summary.failed == 1
    assert summary.retry_scheduled == 0


# ---------------------------------------------------------------------------
# 8. a row waiting on a future next_retry_at is NOT due yet, even though
#    it's PENDING and its scheduled_at has passed.
# ---------------------------------------------------------------------------

def test_row_awaiting_future_retry_window_is_not_due(store, due_video):
    store.update_platform_post(
        store.get_platform_post(due_video.id, "tiktok").id,
        updated_at="2026-01-01T00:00:00+00:00",
        retry_count=1,
        next_retry_at=(NOW + timedelta(minutes=5)).isoformat(),
    )
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.discovered == 0

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PENDING"  # untouched — never claimed


# ---------------------------------------------------------------------------
# 9. a row whose next_retry_at has just arrived (exactly `now`) IS due —
#    inclusive boundary, matching scheduled_at's own inclusive semantics.
# ---------------------------------------------------------------------------

def test_row_at_exact_retry_window_is_due(store, due_video):
    store.update_platform_post(
        store.get_platform_post(due_video.id, "tiktok").id,
        updated_at="2026-01-01T00:00:00+00:00",
        retry_count=1,
        next_retry_at=NOW.isoformat(),
    )
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.discovered == 1
    assert summary.claimed == 1


# ---------------------------------------------------------------------------
# 10. a row whose next_retry_at has already passed IS due.
# ---------------------------------------------------------------------------

def test_row_after_retry_window_is_due(store, due_video):
    store.update_platform_post(
        store.get_platform_post(due_video.id, "tiktok").id,
        updated_at="2026-01-01T00:00:00+00:00",
        retry_count=1,
        next_retry_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.discovered == 1


# ---------------------------------------------------------------------------
# 11. a retry that succeeds on the next attempt ends PUBLISHED with exactly
#     one platform_post_id, never resubmitted twice.
# ---------------------------------------------------------------------------

def test_retry_that_succeeds_ends_published_exactly_once(store, due_video):
    failing = FakePublisher(publish_result=PublishError("net blip", reason_code="NETWORK_ERROR"))
    worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)
    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PENDING" and record.retry_count == 1

    # next_retry_at was computed from wall-clock time, not NOW — force it to
    # a known past value relative to `later` so the retry is deterministically due.
    later = NOW + timedelta(minutes=RETRY_BACKOFF_MINUTES[0] + 1)
    store.update_platform_post(
        record.id, updated_at="2026-01-01T00:00:00+00:00", next_retry_at=(later - timedelta(minutes=1)).isoformat()
    )

    succeeding = FakePublisher()
    summary = worker.run_due_posts_once(store, succeeding, platform="tiktok", now=later)
    assert summary.published == 1
    assert len(succeeding.publish_calls) == 1

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PUBLISHED"
    assert record.platform_post_id == "pub_1"


# ---------------------------------------------------------------------------
# 12. retry_count increments correctly across multiple sequential retryable
#     failures (not just the first one).
# ---------------------------------------------------------------------------

def test_retry_count_increments_across_sequential_failures(store, due_video):
    when = NOW
    for expected_count in range(1, MAX_RETRY_ATTEMPTS):
        failing = FakePublisher(publish_result=PublishError("net blip", reason_code="NETWORK_ERROR"))
        summary = worker.run_due_posts_once(store, failing, platform="tiktok", now=when)
        assert summary.retry_scheduled == 1

        record = store.get_platform_post(due_video.id, "tiktok")
        assert record.retry_count == expected_count
        assert record.status == "PENDING"

        # Force next_retry_at into the past relative to the next iteration's
        # `now` so each successive failure is deterministically due.
        when = when + timedelta(minutes=RETRY_BACKOFF_MINUTES[expected_count - 1] + 1)
        store.update_platform_post(
            record.id, updated_at=f"2026-01-0{expected_count + 1}T00:00:00+00:00",
            next_retry_at=(when - timedelta(minutes=1)).isoformat(),
        )


# ---------------------------------------------------------------------------
# 13. retry exhaustion: once retry_count reaches MAX_RETRY_ATTEMPTS, a
#     further retryable failure becomes terminal FAILED, not another retry —
#     and the final failure_reason is preserved.
# ---------------------------------------------------------------------------

def test_retry_exhaustion_ends_failed_with_final_reason_preserved(store, due_video):
    post = store.get_platform_post(due_video.id, "tiktok")
    store.update_platform_post(post.id, updated_at="2026-01-01T00:00:00+00:00", retry_count=MAX_RETRY_ATTEMPTS)

    failing = FakePublisher(publish_result=PublishError("final blip", reason_code="NETWORK_ERROR"))
    summary = worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.retry_count == MAX_RETRY_ATTEMPTS
    assert "final blip" in record.failure_reason
    assert summary.retry_scheduled == 0
    assert summary.failed == 1


# ---------------------------------------------------------------------------
# 14. worker.py continues processing other due posts after one claimed post
#     fails (retryable) — one failure never aborts the batch.
# ---------------------------------------------------------------------------

def test_worker_continues_batch_after_one_retryable_failure(tmp_path, store):
    def make_video(name, scheduled_offset_minutes):
        video_path = tmp_path / "processed" / name
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake mp4 bytes")
        video = store.insert_video(f"h_{name}", name, f"/incoming/{name}", NOW.isoformat())
        store.update_video(
            video.id,
            canonical_media_path=str(video_path),
            container="mp4", video_codec="h264", audio_codec="aac",
            width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
            caption_text="hello world", caption_source="transcript_auto",
            status="ASSIGNED",
        )
        store.insert_platform_post(
            video.id, "tiktok", created_at=NOW.isoformat(),
            scheduled_at=(NOW - timedelta(minutes=scheduled_offset_minutes)).isoformat(),
        )
        return video

    video_a = make_video("a.mp4", 120)
    video_b = make_video("b.mp4", 60)

    class SequencedPublisher:
        def __init__(self):
            self.publish_calls = []

        def publish(self, video_path, caption):
            self.publish_calls.append(video_path)
            if len(self.publish_calls) == 1:
                raise PublishError("net blip", reason_code="NETWORK_ERROR")
            return PublishResult(platform_post_id="pub_b", status="PROCESSING_UPLOAD")

        def get_status(self, platform_post_id):
            return PublishStatusResult(status="PUBLISH_COMPLETE")

    summary = worker.run_due_posts_once(store, SequencedPublisher(), platform="tiktok", now=NOW)
    assert summary.discovered == 2
    assert summary.claimed == 2
    assert len(summary.errors) == 1  # video_a's failure recorded, not raised out of the loop

    record_a = store.get_platform_post(video_a.id, "tiktok")
    record_b = store.get_platform_post(video_b.id, "tiktok")
    assert record_a.status == "PENDING" and record_a.retry_count == 1
    assert record_b.status == "PUBLISHED"


# ---------------------------------------------------------------------------
# 15. platform_post_id safety: once set, a subsequent worker pass never
#     resubmits — even if a stray retry-eligible state existed beforehand.
# ---------------------------------------------------------------------------

def test_published_row_with_platform_post_id_is_never_resubmitted(store, due_video):
    succeeding = FakePublisher()
    worker.run_due_posts_once(store, succeeding, platform="tiktok", now=NOW)
    assert len(succeeding.publish_calls) == 1
    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PUBLISHED" and record.platform_post_id == "pub_1"

    # Even though scheduled_at is still in the past, a PUBLISHED row is
    # never eligible for due-selection again.
    summary = worker.run_due_posts_once(store, succeeding, platform="tiktok", now=NOW + timedelta(days=1))
    assert summary.discovered == 0
    assert len(succeeding.publish_calls) == 1


# ---------------------------------------------------------------------------
# 16. retry state survives a fresh ContentStore reopening the same SQLite
#     file (persisted, not in-memory/transient).
# ---------------------------------------------------------------------------

def test_retry_state_persists_across_store_reopen(tmp_path, due_video, store):
    failing = FakePublisher(publish_result=PublishError("net blip", reason_code="NETWORK_ERROR"))
    worker.run_due_posts_once(store, failing, platform="tiktok", now=NOW)
    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.retry_count == 1
    assert record.next_retry_at is not None
    db_path = store.db_path if hasattr(store, "db_path") else None

    # Reopen a fresh ContentStore against the same underlying file used by
    # the `store` fixture (tmp_path / "test.db").
    with ContentStore(db_path=tmp_path / "test.db") as reopened:
        reopened_record = reopened.get_platform_post(due_video.id, "tiktok")
        assert reopened_record.retry_count == 1
        assert reopened_record.next_retry_at == record.next_retry_at
        assert reopened_record.status == "PENDING"
