"""Tests for media.processing.process_storage_backed_video — the Milestone
3.4 hosted processing entry point that lets an already-uploaded,
storage-backed video be inspected/transcribed/captioned/scheduled without
requiring a permanent local content/incoming/ or content/processed/|failed/
copy (see docs/decisions/0009-object-storage-media-lifecycle.md "Media
Processing").

Uses a real ffmpeg-synthesized video (same convention as
tests/test_fifo_process_content.py) plus a fake Transcriber, and
LocalStorage — no network dependency. Skipped automatically if ffmpeg/
ffprobe are not on PATH."""

import shutil
import subprocess

import pytest

from content_automation.media import inspection as media
from content_automation.media import media_storage
from content_automation.media import processing as process_content
from content_automation.media.transcription import TranscriptionError, TranscriptResult
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")


class FakeTranscriber:
    def transcribe(self, audio_path):
        return TranscriptResult(text="Built a new prospecting tool this week.", language="en", duration_seconds=1.0)


class FailingTranscriber:
    def transcribe(self, audio_path):
        raise TranscriptionError("simulated faster-whisper failure")


class _NeverCalledStorage(LocalStorage):
    """Spy proving materialize() is never reached when the ownership check
    should fail first — an AssertionError from inside materialize() would
    surface as the test failure, distinct from the expected
    MediaOwnershipError, making a wrong call site obvious."""

    def materialize(self, key):
        raise AssertionError("storage.materialize() must not be called before the ownership check runs")


@pytest.fixture
def synthetic_video(tmp_path):
    path = tmp_path / "video_001.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest",
            "-loglevel", "error",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


@pytest.fixture
def routed_dirs(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    failed = tmp_path / "failed"
    monkeypatch.setattr(process_content, "PROCESSED_DIR", processed)
    monkeypatch.setattr(process_content, "FAILED_DIR", failed)
    return processed, failed


def _seed_open_slot(store, user_id, scheduled_at="2999-01-01T10:00:00"):
    store.insert_slot_if_missing(scheduled_at, None, None, "2026-01-01T00:00:00", user_id=user_id)


def _seed_open_slot_no_owner(store, scheduled_at="2999-01-01T10:00:00"):
    store.insert_slot_if_missing(scheduled_at, None, None, "2026-01-01T00:00:00")


def _uploaded_video_with_no_local_record(store, storage, synthetic_video, user_id):
    """Simulates a video that arrived via a (future) upload API: the row
    exists and is storage-backed from the start — canonical_media_path is
    never set, because there was never a permanent local file to record
    one for. Deliberately does NOT use media_storage.upload_canonical_media
    (that function is for migrating an *already-locally-processed* video —
    see cli/migrate_media_to_object_storage.py — which assumes
    canonical_media_path is already set; this is the other case, upload
    with no local processing history at all)."""
    file_hash = media.file_hash(synthetic_video)
    video = store.insert_video(file_hash, synthetic_video.name, str(synthetic_video), "2026-01-01T00:00:00", user_id=user_id)
    key = media_storage.build_storage_key(user_id, video.id, synthetic_video.suffix)
    storage.put(key, synthetic_video)
    store.update_video(video.id, storage_provider=storage.provider_name, storage_key=key)
    return store.get_video(video.id)


# --- 1. processed with original local source absent ----------------------

def test_storage_backed_video_processed_with_local_source_absent(store, storage, synthetic_video, routed_dirs):
    user = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    video = _uploaded_video_with_no_local_record(store, storage, synthetic_video, user.id)
    _seed_open_slot(store, user.id)
    synthetic_video.unlink()  # the original local source is gone — must not be needed

    outcome = process_content.process_storage_backed_video(
        store, storage, FakeTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert outcome.video.status == "ASSIGNED"
    assert outcome.video.transcript == "Built a new prospecting tool this week."


def test_storage_backed_video_no_permanent_local_copy_created(store, storage, synthetic_video, routed_dirs):
    """The core architecture claim this milestone's feedback asked to
    actually prove: hosted processing must not require (or create) a
    permanent content/processed/|failed/ copy."""
    processed_dir, failed_dir = routed_dirs
    user = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    video = _uploaded_video_with_no_local_record(store, storage, synthetic_video, user.id)
    _seed_open_slot(store, user.id)

    process_content.process_storage_backed_video(
        store, storage, FakeTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="fifo"
    )

    assert not processed_dir.exists() or list(processed_dir.iterdir()) == []
    assert not failed_dir.exists() or list(failed_dir.iterdir()) == []


# --- 2. temp source removed on success ------------------------------------

def test_temp_source_removed_after_successful_processing(store, storage, synthetic_video, routed_dirs):
    user = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    video = _uploaded_video_with_no_local_record(store, storage, synthetic_video, user.id)
    _seed_open_slot(store, user.id)

    outcome = process_content.process_storage_backed_video(
        store, storage, FakeTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert not outcome.path.exists()  # the temp materialization was cleaned up


# --- 3. temp source removed on processing failure -------------------------

def test_temp_source_removed_after_processing_failure(store, storage, synthetic_video, routed_dirs):
    """pillar mode: a transcription failure is a hard (terminal) failure —
    unlike fifo mode, where it's non-blocking enrichment — so this
    exercises process_one's on_failed hook."""
    user = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    video = _uploaded_video_with_no_local_record(store, storage, synthetic_video, user.id)

    outcome = process_content.process_storage_backed_video(
        store, storage, FailingTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="pillar"
    )

    assert outcome.kind == "FAILED"
    assert not outcome.path.exists()  # the temp materialization was cleaned up even on failure


def test_failed_storage_backed_video_leaves_no_local_trace(store, storage, synthetic_video, routed_dirs):
    processed_dir, failed_dir = routed_dirs
    user = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    video = _uploaded_video_with_no_local_record(store, storage, synthetic_video, user.id)

    process_content.process_storage_backed_video(
        store, storage, FailingTranscriber(), None, video.id, user.id, dry_run=False, routing_mode="pillar"
    )

    assert not failed_dir.exists() or list(failed_dir.iterdir()) == []


# --- 4. ownership checked before storage access ---------------------------

def test_ownership_checked_before_storage_access(store, synthetic_video):
    user_a = store.create_user("a@example.com", "A", "2026-01-01T00:00:00")
    user_b = store.create_user("b@example.com", "B", "2026-01-01T00:00:00")
    real_storage = LocalStorage(root=synthetic_video.parent / "objects")
    video = _uploaded_video_with_no_local_record(store, real_storage, synthetic_video, user_a.id)

    spy_storage = _NeverCalledStorage(root=real_storage.root)

    with pytest.raises(media_storage.MediaOwnershipError):
        process_content.process_storage_backed_video(
            store, spy_storage, FakeTranscriber(), None, video.id, user_b.id, dry_run=False, routing_mode="fifo"
        )

    # Nothing about the video changed — the wrong-user attempt had zero effect.
    assert store.get_video(video.id).status == "DISCOVERED"


# --- 5. existing local process_one behavior unchanged ----------------------

def test_local_process_one_behavior_unchanged_by_the_new_hooks(store, synthetic_video, routed_dirs):
    """process_one called exactly as every pre-3.4 caller already does
    (no on_failed/on_assigned args) must still move the file into
    content/processed/ and set canonical_media_path to that location —
    the default hooks must reproduce _move_file's exact pre-3.4 behavior."""
    processed_dir, _ = routed_dirs
    _seed_open_slot_no_owner(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert not synthetic_video.exists()
    expected_path = processed_dir / "video_001.mp4"
    assert expected_path.exists()
    assert outcome.video.canonical_media_path == str(expected_path)
