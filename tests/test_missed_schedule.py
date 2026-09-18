"""Tests for missed-schedule behavior (Milestone 2.1.7): a PENDING
platform_posts row whose scheduled_at has passed while no worker was
available (e.g. the process was offline) is not forgotten — it stays due,
at any distance past its schedule, and is published exactly like any other
due row the next time a worker runs. scheduled_at is never rewritten.

This file deliberately does not re-cover ground already proven elsewhere:
- the 1-hour-overdue and exact-boundary due/not-due cases for PENDING vs.
  PUBLISHED/FAILED/PUBLISHING already live in tests/test_due_post_selector.py.
- the next_retry_at future/exact/elapsed boundary cases already live in
  tests/test_retry_backoff.py.
It adds the missed-schedule-specific cases those files don't: PENDING rows
overdue by days (not just an hour), an end-to-end worker run against a
multi-day-overdue post proving scheduled_at is preserved and published_at
reflects the late execution time, a retry-gated overdue post moving from
not-due to due as time advances past next_retry_at, and the pure
calculate_schedule_delay lateness helper."""

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from content_automation.scheduling import due_post_selector
from content_automation.scheduling import worker
from content_automation.config import TIMEZONE
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishResult, PublishStatusResult

NOW = datetime(2026, 9, 14, 8, 0, 0)
_video_counter = itertools.count()


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def _insert_video(store, name):
    record = store.insert_video(f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())
    return record.id


def _add_platform_post(
    store, *, scheduled_at, status="PENDING", platform="tiktok", platform_post_id=None,
    video_id=None, next_retry_at=None,
):
    if video_id is None:
        video_id = _insert_video(store, f"video-{next(_video_counter)}")
    record = store.insert_platform_post(video_id, platform, created_at=NOW.isoformat(), scheduled_at=scheduled_at)
    fields = {"status": status}
    if platform_post_id is not None:
        fields["platform_post_id"] = platform_post_id
    if next_retry_at is not None:
        fields["next_retry_at"] = next_retry_at
    store.update_platform_post(record.id, updated_at=NOW.isoformat(), **fields)
    return store.get_platform_post(video_id, platform)


@pytest.fixture
def due_video(tmp_path, store):
    """A video already processed and assigned, with a PENDING tiktok
    platform_post scheduled several days in the past — i.e. genuinely due,
    and overdue by more than a routine polling gap, right now."""
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
        video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=(NOW - timedelta(days=3)).isoformat()
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
# Overdue-at-scale due selection
# ---------------------------------------------------------------------------

def test_pending_post_one_minute_overdue_is_due(store):
    post = _add_platform_post(store, scheduled_at=(NOW - timedelta(minutes=1)).isoformat())

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


def test_pending_post_several_hours_overdue_is_due(store):
    post = _add_platform_post(store, scheduled_at=(NOW - timedelta(hours=6)).isoformat())

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


def test_pending_post_several_days_overdue_is_due(store):
    post = _add_platform_post(store, scheduled_at=(NOW - timedelta(days=3)).isoformat())

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


def test_overdue_published_post_is_still_excluded(store):
    _add_platform_post(
        store, scheduled_at=(NOW - timedelta(days=3)).isoformat(), status="PUBLISHED", platform_post_id="pub_1"
    )

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


def test_overdue_failed_post_is_never_picked_up_again(store):
    _add_platform_post(store, scheduled_at=(NOW - timedelta(days=3)).isoformat(), status="FAILED")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


def test_overdue_publishing_post_is_not_ordinary_due_work(store):
    """An overdue PUBLISHING row is a crash-recovery concern (Milestone
    2.1.5), not ordinary due-post detection — see due_post_selector.py's
    module docstring."""
    _add_platform_post(store, scheduled_at=(NOW - timedelta(days=3)).isoformat(), status="PUBLISHING")

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


# ---------------------------------------------------------------------------
# Retry-gated overdue interaction: the original schedule can be arbitrarily
# overdue while the retry backoff window still gates execution.
# ---------------------------------------------------------------------------

def test_overdue_pending_post_with_future_next_retry_at_is_not_due(store):
    _add_platform_post(
        store,
        scheduled_at=(NOW - timedelta(days=1)).isoformat(),
        next_retry_at=(NOW + timedelta(minutes=15)).isoformat(),
    )

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert due == []


def test_overdue_pending_post_becomes_due_once_retry_window_elapses(store):
    post = _add_platform_post(
        store,
        scheduled_at=(NOW - timedelta(days=1)).isoformat(),
        next_retry_at=(NOW - timedelta(minutes=1)).isoformat(),
    )

    due = due_post_selector.get_due_posts(store, "tiktok", now=NOW)

    assert [p.id for p in due] == [post.id]


# ---------------------------------------------------------------------------
# End-to-end worker execution against a multi-day-overdue post
# ---------------------------------------------------------------------------

def test_worker_executes_an_overdue_eligible_post(store, due_video):
    publisher = FakePublisher()

    summary = worker.run_due_posts_once(store, publisher, now=NOW)

    assert summary.discovered == 1
    assert summary.claimed == 1
    assert summary.published == 1
    assert len(publisher.publish_calls) == 1


def test_worker_does_not_modify_scheduled_at_on_overdue_publish(store, due_video):
    publisher = FakePublisher()
    original = store.get_platform_post(due_video.id, "tiktok")

    worker.run_due_posts_once(store, publisher, now=NOW)

    final = store.get_platform_post(due_video.id, "tiktok")
    assert final.scheduled_at == original.scheduled_at
    assert final.scheduled_at == (NOW - timedelta(days=3)).isoformat()


def test_overdue_publish_preserves_original_scheduled_at_and_published_at_is_later(store, due_video):
    publisher = FakePublisher()

    worker.run_due_posts_once(store, publisher, now=NOW)

    final = store.get_platform_post(due_video.id, "tiktok")
    assert final.status == "PUBLISHED"
    assert final.published_at is not None
    delay = due_post_selector.calculate_schedule_delay(final.scheduled_at, final.published_at)
    assert delay > timedelta(0)


# ---------------------------------------------------------------------------
# calculate_schedule_delay: pure lateness derivation.
#
# scheduled_at is stored as naive local time (config.TIMEZONE); published_at
# is stored as aware UTC (publish_tiktok/crash_recovery's _now_iso(), same
# convention as updated_at) — a genuine pre-existing mismatch between the
# two columns. These tests build published_at the way the real pipeline
# does: a local instant converted to aware UTC.
# ---------------------------------------------------------------------------

def _as_aware_utc(local_naive: datetime) -> str:
    return local_naive.replace(tzinfo=ZoneInfo(TIMEZONE)).astimezone(timezone.utc).isoformat()


def test_calculate_schedule_delay_on_time_publish_is_zero():
    scheduled_at = NOW.isoformat()
    published_at = _as_aware_utc(NOW)

    assert due_post_selector.calculate_schedule_delay(scheduled_at, published_at) == timedelta(0)


def test_calculate_schedule_delay_late_publish_is_positive():
    scheduled_at = NOW.isoformat()
    published_at = _as_aware_utc(NOW + timedelta(hours=2, minutes=32))

    delay = due_post_selector.calculate_schedule_delay(scheduled_at, published_at)

    assert delay == timedelta(hours=2, minutes=32)
