"""Tests for worker.py: the one-pass discover -> claim -> execute path
(Milestone 2.1.4). A FakePublisher stands in for TikTokPublisher — no real
network access or TikTok credentials required. Uses a real (temp-file)
ContentStore, since this is precisely about what content_store state
results across the whole pipeline, not just what the publisher was called
with."""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from content_automation.scheduling import worker
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


# 1. due PENDING post is discovered
def test_due_pending_post_is_discovered(store, due_video):
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.discovered == 1


# 2. due post is atomically claimed
def test_due_post_is_claimed(store, due_video):
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.claimed == 1


# 3. claimed post executes publisher exactly once
def test_claimed_post_executes_publisher_exactly_once(store, due_video):
    publisher = FakePublisher()
    worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW)
    assert len(publisher.publish_calls) == 1
    assert publisher.publish_calls[0] == (Path(due_video.canonical_media_path), "hello world")


# 4 & 5. successful execution ends PUBLISHED and persists platform_post_id
def test_successful_execution_ends_published_with_platform_post_id(store, due_video):
    worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "PUBLISHED"
    assert record.platform_post_id == "pub_1"


def test_summary_counts_one_published(store, due_video):
    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)
    assert summary.published == 1
    assert summary.failed == 0


# 6. failed execution ends FAILED
def test_failed_execution_ends_failed(store, due_video):
    publisher = FakePublisher(publish_result=PublishError("upload rejected", reason_code="UPLOAD_FAILED"))

    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW)

    record = store.get_platform_post(due_video.id, "tiktok")
    assert record.status == "FAILED"
    assert summary.failed == 1
    assert summary.published == 0
    assert len(summary.errors) == 1


# 7. failed claim causes skip, not publish
def test_already_claimed_row_is_not_discovered_and_never_published(store, due_video):
    """A row already claimed (e.g. by a concurrently running worker or a
    manual publish_tiktok.py invocation) before this worker's discovery
    step runs is PUBLISHING, and due_post_selector (PENDING-only, corrected
    in 2.1.2) will not even return it — the worker never attempts a claim
    on it, and never calls the publisher. The genuine race — where
    discovery and claiming for the *same* row happen concurrently across
    two real workers — is covered by test_two_concurrent_workers_do_not_double_publish
    below, which is the stronger, more realistic version of this
    requirement (a claim actually failing due to a real race, not just a
    row that was never discovered in the first place)."""
    record = store.get_platform_post(due_video.id, "tiktok")
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())

    publisher = FakePublisher()
    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW)

    assert summary.discovered == 0
    assert summary.claimed == 0
    assert len(publisher.publish_calls) == 0


# 8. two competing worker invocations do not double-publish the same post
def test_two_concurrent_workers_do_not_double_publish(tmp_path):
    """Real two-thread, two-connection race — the same integration-level
    invariant Milestone 2.1.3 proved for claim_platform_post() alone, now
    proven across the full discover -> claim -> execute pipeline."""
    db_path = tmp_path / "concurrent.db"
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    with ContentStore(db_path=db_path) as setup_store:
        video = setup_store.insert_video("h1", "video.mp4", "/incoming/video.mp4", NOW.isoformat())
        setup_store.update_video(
            video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264",
            audio_codec="aac", width=576, height=1024, fps=30.0, duration_seconds=20.0,
            file_size_bytes=video_path.stat().st_size, caption_text="hello world",
            caption_source="transcript_auto", status="ASSIGNED",
        )
        setup_store.insert_platform_post(
            video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=(NOW - timedelta(hours=1)).isoformat()
        )

    shared_publisher = FakePublisher()  # simple list.append() calls are GIL-safe to share across threads
    barrier = threading.Barrier(2)

    def run_worker():
        with ContentStore(db_path=db_path) as worker_store:
            barrier.wait()  # maximize actual overlap between the two claim attempts
            worker.run_due_posts_once(worker_store, shared_publisher, platform="tiktok", now=NOW)

    threads = [threading.Thread(target=run_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(shared_publisher.publish_calls) == 1  # published exactly once, never twice

    with ContentStore(db_path=db_path) as verify_store:
        record = verify_store.get_platform_post(video.id, "tiktok")
    assert record.status == "PUBLISHED"


# 9. PUBLISHED post is not re-executed
def test_published_post_is_not_re_executed(store, due_video):
    worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)  # first pass: publishes it
    publisher = FakePublisher()

    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW)  # second pass

    assert summary.discovered == 0  # PUBLISHED is not due work
    assert len(publisher.publish_calls) == 0


# 10. PUBLISHING post is not treated as ordinary due work
def test_publishing_post_is_not_treated_as_due_work(store, due_video):
    record = store.get_platform_post(due_video.id, "tiktok")
    store.claim_platform_post(record.id, updated_at=NOW.isoformat())  # now PUBLISHING

    summary = worker.run_due_posts_once(store, FakePublisher(), platform="tiktok", now=NOW)

    assert summary.discovered == 0
