"""Milestone 3.4, Phase 29: end-to-end simulation against real Postgres +
real Supabase Storage + FakePublisher — the two most important pieces of
persistent hosted state (Postgres, Milestone 3.3; object storage,
Milestone 3.4) proven working together, through the actual production
modules (slot_matcher, platform_post_materializer, worker, media_storage),
not reimplemented test logic.

Skipped unless both DATABASE_URL and SUPABASE_SERVICE_ROLE_KEY are
configured. Runs against config.POSTGRES_TEST_SCHEMA and
config.SUPABASE_STORAGE_TEST_BUCKET — never the real application schema/
bucket. No live TikTok call — FakePublisher throughout."""

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from content_automation.config import DATABASE_URL, POSTGRES_TEST_SCHEMA, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_TEST_BUCKET
from content_automation.media import media_storage
from content_automation.media import processing as process_content
from content_automation.media.transcription import TranscriptResult
from content_automation.persistence.postgres_content_store import PostgresContentStore
from content_automation.publishing.publisher import PublishResult, PublishStatusResult
from content_automation.scheduling import platform_post_materializer, slot_matcher, worker
from content_automation.scheduling.slot_matcher import now_in_config_timezone
from content_automation.storage.supabase_storage import SupabaseStorage

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not (DATABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
    reason="DATABASE_URL and/or SUPABASE_SERVICE_ROLE_KEY not configured — end-to-end hosted test skipped",
)

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture(scope="session", autouse=True)
def _reset_test_schema():
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute(f'DROP SCHEMA IF EXISTS "{POSTGRES_TEST_SCHEMA}" CASCADE')
    conn.close()
    yield


@pytest.fixture
def store():
    with PostgresContentStore(dsn=DATABASE_URL, schema=POSTGRES_TEST_SCHEMA) as s:
        s._conn.execute(
            "TRUNCATE platform_posts, content_slots, videos, platform_connections, "
            "auth_identities, users RESTART IDENTITY CASCADE"
        )
        yield s


@pytest.fixture
def storage():
    return SupabaseStorage(bucket=SUPABASE_STORAGE_TEST_BUCKET)


@pytest.fixture
def cleanup_keys(storage):
    keys = []
    yield keys
    for key in keys:
        storage.delete(key)


@dataclass
class FakePublisher:
    publish_result: PublishResult = field(
        default_factory=lambda: PublishResult(platform_post_id="pg_storage_pub_1", status="PROCESSING_UPLOAD")
    )
    status_result: PublishStatusResult = field(default_factory=lambda: PublishStatusResult(status="PUBLISH_COMPLETE"))
    publish_calls: list = field(default_factory=list)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        self.publish_calls.append((video_path, caption, video_path.read_bytes()))
        return self.publish_result

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        return self.status_result


class FakeTranscriber:
    def transcribe(self, audio_path):
        return TranscriptResult(text="Real hosted processing stage.", language="en", duration_seconds=1.0)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")
def test_upload_through_processing_to_published_against_real_postgres_and_real_object_storage(
    store, storage, tmp_path, cleanup_keys,
):
    """The fuller version of the test below: starts from a real
    ffmpeg-synthesized video that has NEVER been locally processed
    (uploaded to real object storage as-is, no canonical_media_path ever
    recorded), runs it through the real hosted processing entry point
    (media.processing.process_storage_backed_video — real inspection via
    ffprobe, a fake transcriber only to avoid a real Whisper model
    download in a test, real caption derivation, real FIFO slot
    assignment against real Postgres, real object-storage
    materialize/cleanup) and only then continues into the worker/publish
    stage — proving the whole hosted pipeline end to end, not just its
    back half. See test_upload_to_published_against_real_postgres_and_real_object_storage
    below for the narrower publish-only version this one builds on."""
    user = store.get_or_create_local_user()

    video_path = tmp_path / "hosted_processing.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest",
            "-loglevel", "error",
            str(video_path),
        ],
        check=True,
    )

    # --- upload (never locally processed — no canonical_media_path is ever set) ---
    file_hash = process_content.media.file_hash(video_path)
    video = store.insert_video(file_hash, video_path.name, str(video_path), NOW.isoformat(), user_id=user.id)
    key = media_storage.build_storage_key(user.id, video.id, video_path.suffix)
    storage.put(key, video_path)
    cleanup_keys.append(key)
    store.update_video(video.id, storage_provider=storage.provider_name, storage_key=key)
    video_path.unlink()  # prove processing reads from real object storage, not this local file

    # --- real hosted processing stage: real ffprobe inspection, caption,
    #     FIFO slot assignment — all against real Postgres + real Supabase
    #     Storage, through the real production entry point.
    #     process_storage_backed_video/process_one's FIFO slot matching has
    #     no injectable `now` (unlike worker.run_due_posts_once) — it
    #     always uses real wall-clock time, so the seeded slot must be a
    #     real near-future time, not relative to this fixture's own
    #     fictional NOW. ---
    real_now = now_in_config_timezone()
    slot_scheduled_at = (real_now + timedelta(minutes=1)).isoformat()
    store.insert_slot_if_missing(slot_scheduled_at, None, None, NOW.isoformat(), user_id=user.id)

    outcome = process_content.process_storage_backed_video(
        store, storage, FakeTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert not outcome.path.exists()  # temp materialization cleaned up after processing
    assert store.get_video(video.id).transcript == "Real hosted processing stage."

    # --- worker materializes media from real object storage again (a
    #     second, independent materialize — proving the object, not a
    #     leftover temp file from processing, is what's actually durable),
    #     FakePublisher publishes. `now` must be at/after the real
    #     slot_scheduled_at above (real wall-clock time), not the
    #     fixture's fictional NOW. ---
    publisher = FakePublisher()
    worker_now = real_now + timedelta(minutes=5)
    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=worker_now, user_id=user.id, storage=storage)

    assert summary.published == 1
    published_path, caption, published_bytes = publisher.publish_calls[0]
    assert not published_path.exists()
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"


def test_upload_to_published_against_real_postgres_and_real_object_storage(store, storage, tmp_path, cleanup_keys):
    user = store.get_or_create_local_user()

    # --- upload/store media ---
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"real hosted pipeline bytes" * 500)
    video = store.insert_video("hash-hosted-e2e", "clip.mp4", str(video_path), NOW.isoformat(), user_id=user.id)
    store.update_video(
        video.id, canonical_media_path=str(video_path), container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="real hosted pipeline", caption_source="transcript_auto", status="TRANSCRIBED",
    )

    # --- persist storage reference (upload to real object storage) ---
    updated = media_storage.upload_canonical_media(store, storage, video.id, user.id)
    cleanup_keys.append(updated.storage_key)
    assert updated.storage_provider == "supabase"
    video_path.unlink()  # prove everything from here on reads from object storage, not this local file

    # --- materialize locally / inspect/process (proven separately in
    #     test_media_storage.py and test_storage_supabase.py; here we only
    #     need the assign+materialize+publish path) ---
    past_scheduled_at = (NOW - timedelta(hours=1)).isoformat()
    store.insert_slot_if_missing(past_scheduled_at, None, None, NOW.isoformat(), user_id=user.id)
    slot = slot_matcher.select_slot_fifo(store, now=NOW - timedelta(hours=2), user_id=user.id)
    store.assign_slot(video.id, slot.id)
    platform_post_materializer.materialize_platform_posts_for_assignment(
        store, video.id, slot.id, NOW.isoformat(), user_id=user.id
    )

    # --- worker materializes media from real object storage, FakePublisher publishes ---
    publisher = FakePublisher()
    summary = worker.run_due_posts_once(store, publisher, platform="tiktok", now=NOW, user_id=user.id, storage=storage)

    assert summary.published == 1
    published_path, caption, published_bytes = publisher.publish_calls[0]
    assert published_bytes == b"real hosted pipeline bytes" * 500
    assert caption == "real hosted pipeline"
    assert not published_path.exists()  # temp materialization cleaned up
    assert store.get_platform_post(video.id, "tiktok").status == "PUBLISHED"
