"""Tests for crash_recovery.py: one-pass recovery of stale PUBLISHING
platform_posts rows (Milestone 2.1.5). A FakePublisher stands in for
TikTokPublisher — no real network access or TikTok credentials required.
Uses a real (temp-file) ContentStore."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from content_automation.scheduling import crash_recovery as cr
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishResult, PublishStatusResult

NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@dataclass
class FakePublisher:
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


def _insert_video(store, name="video"):
    return store.insert_video(f"h-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat())


# ---------------------------------------------------------------------------
# staleness / discovery
# ---------------------------------------------------------------------------

def test_recent_publishing_row_is_not_recovered(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=5)).isoformat())

    summary = cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    assert summary.discovered == 0
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHING"


def test_stale_publishing_row_is_discovered(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())

    summary = cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    assert summary.discovered == 1


@pytest.mark.parametrize("status", ["PENDING", "PUBLISHED", "FAILED"])
def test_non_publishing_rows_are_never_recovered(store, status):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.update_platform_post(record.id, updated_at=(NOW - timedelta(hours=2)).isoformat(), status=status)

    summary = cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    assert summary.discovered == 0


# ---------------------------------------------------------------------------
# Case A — stale PUBLISHING, no platform_post_id -> requeue to PENDING
# ---------------------------------------------------------------------------

def test_case_a_requeues_to_pending(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())

    summary = cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    assert summary.requeued == 1
    updated = store.get_platform_post(video.id, "tiktok")
    assert updated.status == "PENDING"
    assert updated.platform_post_id is None


def test_case_a_requeued_post_can_be_claimed_again(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())

    cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    claimed_again = store.claim_platform_post(record.id, updated_at=NOW.isoformat())
    assert claimed_again is True
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHING"


def test_case_a_never_calls_publisher(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())
    publisher = FakePublisher()

    cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert len(publisher.publish_calls) == 0
    assert len(publisher.status_calls) == 0


# ---------------------------------------------------------------------------
# Case B — stale PUBLISHING, platform_post_id set -> poll, never resubmit
# ---------------------------------------------------------------------------

def _stale_submitted_row(store, video):
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=50)).isoformat())
    store.update_platform_post(
        record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat(), platform_post_id="pub_1"
    )
    return store.get_platform_post(video.id, "tiktok")


def test_case_b_never_calls_publish(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher()

    cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert len(publisher.publish_calls) == 0  # media is never resubmitted


def test_case_b_polls_status_exactly_once(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher()

    cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert publisher.status_calls == ["pub_1"]


def test_case_b_poll_success_ends_published(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))

    summary = cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert summary.published == 1
    record = store.get_platform_post(video.id, "tiktok")
    assert record.status == "PUBLISHED"
    assert record.platform_post_id == "pub_1"


def test_case_b_poll_terminal_failure_ends_failed(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher(status_result=PublishStatusResult(status="FAILED", failure_reason="video_pull_failed"))

    summary = cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert summary.failed == 1
    record = store.get_platform_post(video.id, "tiktok")
    assert record.status == "FAILED"
    assert record.failure_reason == "video_pull_failed"
    assert record.platform_post_id == "pub_1"  # preserved


def test_case_b_poll_still_processing_remains_publishing(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher(status_result=PublishStatusResult(status="PROCESSING_DOWNLOAD"))

    summary = cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert summary.still_processing == 1
    record = store.get_platform_post(video.id, "tiktok")
    assert record.status == "PUBLISHING"
    assert record.platform_post_id == "pub_1"


# ---------------------------------------------------------------------------
# atomic recovery safety
# ---------------------------------------------------------------------------

def test_recovery_does_not_overwrite_a_row_that_changed_after_selection(store):
    """The core Phase 6 invariant: recovery may act only on a row that is
    still the stale record it inspected. Simulates the row having been
    touched (e.g. resumed by the original worker) between selection and
    the recovery write, by mutating it directly with the store — recovery
    must not clobber that change."""
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())

    stale_rows = store.get_recoverable_platform_posts(
        "tiktok", stale_before_iso=(NOW - timedelta(minutes=30)).isoformat()
    )
    assert len(stale_rows) == 1  # sanity: it was genuinely selected as stale

    # The row changes state after selection but before recovery acts —
    # simulating the original worker actually finishing the job itself.
    store.update_platform_post(
        record.id, updated_at=(NOW - timedelta(minutes=1)).isoformat(),
        status="PUBLISHED", platform_post_id="pub_from_original_worker", published_at=NOW.isoformat(),
    )

    publisher = FakePublisher()
    summary = cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert len(publisher.publish_calls) == 0
    assert len(publisher.status_calls) == 0  # discovery re-ran and correctly found nothing stale anymore
    final = store.get_platform_post(video.id, "tiktok")
    assert final.status == "PUBLISHED"
    assert final.platform_post_id == "pub_from_original_worker"  # untouched by recovery


def test_recovery_does_not_create_duplicate_platform_posts(store):
    video = _insert_video(store)
    record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
    store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=45)).isoformat())

    cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    rows = store._conn.execute(
        "SELECT COUNT(*) AS n FROM platform_posts WHERE video_id = ? AND platform = 'tiktok'", (video.id,)
    ).fetchone()
    assert rows["n"] == 1


def test_recovery_never_calls_publish_when_platform_post_id_exists(store):
    video = _insert_video(store)
    _stale_submitted_row(store, video)
    publisher = FakePublisher()

    cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert len(publisher.publish_calls) == 0


# ---------------------------------------------------------------------------
# Phase 8 — local crash simulations (real fixtures, not just isolated units)
# ---------------------------------------------------------------------------

def test_scenario_a_claimed_then_crashed_before_publish_recovers_to_pending(tmp_path):
    """PENDING -> claim -> (artificially stop before publisher.publish()) ->
    age beyond stale threshold -> recover -> expect PENDING -> can be
    claimed again."""
    with ContentStore(db_path=tmp_path / "scenario_a.db") as store:
        video = _insert_video(store, "scenario-a")
        record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())

        claimed = store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=60)).isoformat())
        assert claimed is True
        # Deliberately do NOT call publisher.publish() here — simulates the
        # process dying immediately after claiming, before submission.

        summary = cr.recover_stale_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

        assert summary.requeued == 1
        recovered = store.get_platform_post(video.id, "tiktok")
        assert recovered.status == "PENDING"
        assert recovered.platform_post_id is None

        reclaimed = store.claim_platform_post(record.id, updated_at=NOW.isoformat())
        assert reclaimed is True
        assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHING"


def test_scenario_b_submitted_then_crashed_before_poll_recovers_to_published(tmp_path):
    """PENDING -> claim -> platform_post_id persisted (submission
    succeeded) -> (simulate crash before polling completion) -> age beyond
    stale threshold -> recover with a FakePublisher reporting completion ->
    expect PUBLISHED. publisher.publish() must never be invoked during
    recovery."""
    with ContentStore(db_path=tmp_path / "scenario_b.db") as store:
        video = _insert_video(store, "scenario-b")
        record = store.insert_platform_post(video.id, "tiktok", created_at=NOW.isoformat())
        store.claim_platform_post(record.id, updated_at=(NOW - timedelta(minutes=65)).isoformat())
        # Simulates execute_claimed_platform_post() having reached the
        # "persist platform_post_id immediately" step and then crashing
        # before the poll ever ran.
        store.update_platform_post(
            record.id, updated_at=(NOW - timedelta(minutes=60)).isoformat(), platform_post_id="pub_scenario_b"
        )

        publisher = FakePublisher(status_result=PublishStatusResult(status="PUBLISH_COMPLETE"))
        summary = cr.recover_stale_posts_once(store, publisher, platform="tiktok", now=NOW)

        assert len(publisher.publish_calls) == 0  # never resubmitted
        assert summary.published == 1
        recovered = store.get_platform_post(video.id, "tiktok")
        assert recovered.status == "PUBLISHED"
        assert recovered.platform_post_id == "pub_scenario_b"
