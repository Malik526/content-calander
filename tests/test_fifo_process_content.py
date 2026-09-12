"""End-to-end pipeline tests for FIFO routing mode (Milestone 1.3), using a
real ffmpeg-synthesized video plus a fake Transcriber (no Whisper model
download, no classifier involved at all — FIFO mode never constructs or
calls one).

Skipped automatically if ffmpeg/ffprobe are not on PATH.
"""

import shutil
import subprocess
import sys

import pytest

import classification
import media
import process_content
from content_store import ContentStore
from transcription import TranscriptionError, TranscriptResult

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")


class FakeTranscriber:
    def transcribe(self, audio_path):
        return TranscriptResult(text="Built a new prospecting tool this week.", language="en", duration_seconds=1.0)


class FailingTranscriber:
    def transcribe(self, audio_path):
        raise TranscriptionError("simulated faster-whisper failure")


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
def routed_dirs(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    failed = tmp_path / "failed"
    monkeypatch.setattr(process_content, "PROCESSED_DIR", processed)
    monkeypatch.setattr(process_content, "FAILED_DIR", failed)
    return processed, failed


def _seed_open_slot(store, scheduled_at="2999-01-01T10:00:00"):
    store.insert_slot_if_missing(scheduled_at, None, None, "2026-01-01T00:00:00")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_fifo_video_is_assigned_without_a_classifier(store, synthetic_video, routed_dirs):
    """classifier=None is passed through; if FIFO mode ever tried to call
    .classify() on it this would raise AttributeError instead of assigning."""
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert outcome.video.status == "ASSIGNED"
    assert outcome.video.classified_pillar is None
    assert not synthetic_video.exists()
    assert (processed_dir / "video_001.mp4").exists()


def test_fifo_transcript_is_persisted(store, synthetic_video, routed_dirs):
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.video.transcript == "Built a new prospecting tool this week."
    assert outcome.video.transcription_status == "COMPLETE"


def test_fifo_caption_is_derived_from_transcript_by_default(store, synthetic_video, routed_dirs):
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.video.caption_source == "transcript_auto"
    assert outcome.video.caption_text == "Built a new prospecting tool this week."


def test_fifo_no_matching_slot_leaves_video_waiting(store, synthetic_video, routed_dirs):
    # No slot seeded.
    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "WAITING_FOR_SLOT"
    assert outcome.video.assigned_slot_id is None
    assert synthetic_video.exists()


def test_fifo_dry_run_does_not_mutate_slot_or_move_file(store, synthetic_video, routed_dirs):
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=True, routing_mode="fifo"
    )

    assert outcome.kind == "WOULD_ASSIGN"
    assert synthetic_video.exists()
    assert not (processed_dir / "video_001.mp4").exists()
    assert store.find_earliest_open_slot_fifo("1970-01-01T00:00:00") is not None


def test_fifo_rerun_does_not_duplicate_assignment(store, synthetic_video, routed_dirs):
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)
    transcriber = FakeTranscriber()

    first = process_content.process_one(store, transcriber, None, synthetic_video, dry_run=False, routing_mode="fifo")
    assert first.kind == "ASSIGNED"

    moved_path = processed_dir / "video_001.mp4"
    second = process_content.process_one(store, transcriber, None, moved_path, dry_run=False, routing_mode="fifo")

    assert second.kind == "ALREADY_ASSIGNED"
    slots = store._conn.execute("SELECT COUNT(*) AS n FROM content_slots WHERE status = 'ASSIGNED'").fetchone()
    assert slots["n"] == 1


# ---------------------------------------------------------------------------
# Transcription decoupled from FIFO scheduling
# ---------------------------------------------------------------------------

def test_fifo_transcription_failure_does_not_block_scheduling(store, synthetic_video, routed_dirs):
    """The core FIFO invariant: a valid, inspected video still claims a
    future slot even when faster-whisper fails. Transcription failure is
    recorded, not silently discarded, and the caption falls back to 'none'
    since there is no transcript to derive one from."""
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FailingTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.kind == "ASSIGNED"
    assert outcome.video.status == "ASSIGNED"
    assert outcome.video.transcript is None
    assert outcome.video.transcription_status == "FAILED"
    assert outcome.video.failure_reason == "TRANSCRIPTION_FAILED"
    assert outcome.video.caption_source == "none"
    assert outcome.video.caption_text is None
    assert (processed_dir / "video_001.mp4").exists()  # still moved to processed, not failed/


def test_fifo_transcription_failure_retries_on_rerun_until_scheduled(store, synthetic_video, routed_dirs):
    """No slot available on the first attempt: transcription failure is
    recorded once; a rerun after a slot opens up schedules the video without
    re-attempting transcription (idempotent) and without ever raising."""
    failing = FailingTranscriber()

    first = process_content.process_one(store, failing, None, synthetic_video, dry_run=False, routing_mode="fifo")
    assert first.kind == "WAITING_FOR_SLOT"
    assert first.video.status == "TRANSCRIPTION_FAILED"

    _seed_open_slot(store)
    second = process_content.process_one(store, failing, None, synthetic_video, dry_run=False, routing_mode="fifo")

    assert second.kind == "ASSIGNED"
    assert second.video.transcription_status == "FAILED"


def test_pillar_mode_still_blocks_on_transcription_failure(store, synthetic_video, routed_dirs):
    """Regression: pillar mode's transcription failure handling is
    unchanged — still a hard FAILED, moved to failed/."""
    _, failed_dir = routed_dirs
    store.insert_slot_if_missing("2999-01-01T10:00:00", "engineering", "p", "2026-01-01T00:00:00")

    class UnusedClassifier:
        def classify(self, *a, **k):
            raise AssertionError("classifier must not be reached — transcription failed first")

    outcome = process_content.process_one(
        store, FailingTranscriber(), UnusedClassifier(), synthetic_video, dry_run=False, routing_mode="pillar"
    )

    assert outcome.kind == "FAILED"
    assert outcome.video.status == "FAILED"
    assert outcome.video.failure_reason == "TRANSCRIPTION_FAILED"
    assert (failed_dir / "video_001.mp4").exists()


# ---------------------------------------------------------------------------
# Caption modes (integration, on top of caption.py's own unit tests)
# ---------------------------------------------------------------------------

def test_manual_caption_mode_never_writes_caption_text(store, synthetic_video, routed_dirs, monkeypatch):
    monkeypatch.setattr(process_content, "CAPTION_MODE", "manual")
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.video.caption_source == "manual"
    assert outcome.video.caption_text is None


def test_manual_caption_mode_does_not_overwrite_a_preexisting_manual_caption(store, synthetic_video, routed_dirs, monkeypatch):
    """Simulates a future editing UI having already set a manual caption
    before the pipeline runs — the caption stage must skip entirely."""
    monkeypatch.setattr(process_content, "CAPTION_MODE", "manual")
    _seed_open_slot(store)

    file_hash = media.file_hash(synthetic_video)
    video = store.insert_video(file_hash, synthetic_video.name, str(synthetic_video), "2026-01-01T00:00:00")
    store.update_video(video.id, caption_text="Hand-written caption", caption_source="manual")

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.video.caption_text == "Hand-written caption"
    assert outcome.video.caption_source == "manual"


def test_none_caption_mode_produces_no_caption(store, synthetic_video, routed_dirs, monkeypatch):
    monkeypatch.setattr(process_content, "CAPTION_MODE", "none")
    _seed_open_slot(store)

    outcome = process_content.process_one(
        store, FakeTranscriber(), None, synthetic_video, dry_run=False, routing_mode="fifo"
    )

    assert outcome.video.caption_source == "none"
    assert outcome.video.caption_text is None


def test_caption_stage_is_idempotent_across_reruns(store, synthetic_video, routed_dirs):
    """Rerunning must not recompute/overwrite an already-set caption."""
    transcriber = FakeTranscriber()

    first = process_content.process_one(store, transcriber, None, synthetic_video, dry_run=False, routing_mode="fifo")
    assert first.kind == "WAITING_FOR_SLOT"
    assert first.video.caption_source == "transcript_auto"

    _seed_open_slot(store)
    second = process_content.process_one(store, transcriber, None, synthetic_video, dry_run=False, routing_mode="fifo")

    assert second.video.caption_text == first.video.caption_text
    assert second.video.caption_source == "transcript_auto"


# ---------------------------------------------------------------------------
# FIFO ordering durability (Milestone 1.3)
# ---------------------------------------------------------------------------

def test_discover_videos_orders_a_known_path_by_created_at_not_current_mtime(store, tmp_path):
    """A video already known to the store (still waiting in incoming/) must
    keep its original discovery order even if its file is later touched,
    because discover_videos sorts a known path by videos.created_at, not by
    the file's current mtime."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    older_by_touch = incoming / "later_touch.mp4"
    newer_by_touch = incoming / "earlier_touch.mp4"
    older_by_touch.write_bytes(b"a")
    newer_by_touch.write_bytes(b"b")

    # Register both as already-known, older_by_touch discovered first (earlier created_at).
    store.insert_video("hash-older", older_by_touch.name, str(older_by_touch), "2026-01-01T00:00:01")
    store.insert_video("hash-newer", newer_by_touch.name, str(newer_by_touch), "2026-01-01T00:00:02")

    # Now touch older_by_touch so its filesystem mtime becomes the newest —
    # a naive mtime sort would now put it last.
    import os
    import time
    time.sleep(0.01)
    os.utime(older_by_touch, None)

    ordered = process_content.discover_videos(store, incoming)

    assert [p.name for p in ordered] == ["later_touch.mp4", "earlier_touch.mp4"]


def test_discover_videos_falls_back_to_mtime_for_genuinely_new_files(store, tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    first = incoming / "first.mp4"
    second = incoming / "second.mp4"
    first.write_bytes(b"a")
    import time
    time.sleep(0.01)
    second.write_bytes(b"b")

    ordered = process_content.discover_videos(store, incoming)

    assert [p.name for p in ordered] == ["first.mp4", "second.mp4"]


def test_discover_videos_sorts_known_videos_before_unknown_ones(store, tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    known = incoming / "known.mp4"
    unknown = incoming / "unknown.mp4"
    known.write_bytes(b"a")
    unknown.write_bytes(b"b")

    store.insert_video("hash-known", known.name, str(known), "2026-01-01T00:00:00")

    ordered = process_content.discover_videos(store, incoming)

    assert [p.name for p in ordered] == ["known.mp4", "unknown.mp4"]


# ---------------------------------------------------------------------------
# Classifier is never constructed in FIFO mode
# ---------------------------------------------------------------------------

def test_fifo_mode_never_constructs_a_classifier(monkeypatch, tmp_path, capsys):
    """An invalid/unset CONTENT_CALENDAR_CLASSIFIER must not matter in FIFO
    mode — classification.build_classifier() is never even called."""
    monkeypatch.setattr(sys, "argv", ["process_content.py"])
    monkeypatch.setattr(media, "check_ffmpeg_available", lambda: None)
    monkeypatch.setattr(process_content, "ROUTING_MODE", "fifo")
    monkeypatch.setattr(process_content, "INCOMING_DIR", tmp_path / "incoming")
    monkeypatch.setattr(process_content, "ContentStore", lambda: ContentStore(db_path=tmp_path / "test.db"))

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("classification.build_classifier must not be called in FIFO mode")

    monkeypatch.setattr(classification, "build_classifier", _must_not_be_called)

    process_content.main()  # must not raise

    assert "No videos found" in capsys.readouterr().out
