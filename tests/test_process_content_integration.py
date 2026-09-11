"""End-to-end pipeline tests using a real ffmpeg-synthesized video plus fake
Transcriber/Classifier (so tests stay fast, deterministic, and offline — no
Whisper model download, no Claude API call).

Skipped automatically if ffmpeg/ffprobe are not on PATH.
"""

import shutil
import subprocess

import pytest

import process_content
from classification import ClassificationResult
from content_store import ContentStore
from transcription import TranscriptResult

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")


class FakeTranscriber:
    def transcribe(self, audio_path):
        return TranscriptResult(text="Built a new prospecting tool this week.", language="en", duration_seconds=1.0)


class FakeClassifier:
    def __init__(self, pillar="building", confidence=0.94, reason="Discusses building a tool."):
        self.pillar = pillar
        self.confidence = confidence
        self.reason = reason
        self.calls = 0

    def classify(self, transcript, pillars):
        self.calls += 1
        return ClassificationResult(pillar=self.pillar, confidence=self.confidence, reason=self.reason)


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


def _seed_open_slot(store, pillar="building", scheduled_at="2999-01-01T10:00:00"):
    store.insert_slot_if_missing(scheduled_at, pillar, "prompt", "2026-01-01T00:00:00")


def test_high_confidence_video_is_assigned_and_moved(store, synthetic_video, routed_dirs):
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)
    classifier = FakeClassifier(confidence=0.94)

    outcome = process_content.process_one(
        store, FakeTranscriber(), classifier, synthetic_video, dry_run=False
    )

    assert outcome.kind == "ASSIGNED"
    assert outcome.video.status == "ASSIGNED"
    assert not synthetic_video.exists()
    assert (processed_dir / "video_001.mp4").exists()


def test_low_confidence_video_becomes_needs_review(store, synthetic_video, routed_dirs):
    _seed_open_slot(store)
    classifier = FakeClassifier(confidence=0.43, reason="Ambiguous content.")

    outcome = process_content.process_one(
        store, FakeTranscriber(), classifier, synthetic_video, dry_run=False
    )

    assert outcome.kind == "NEEDS_REVIEW"
    assert outcome.video.status == "NEEDS_REVIEW"
    # File is left in place for review, not moved.
    assert synthetic_video.exists()


def test_no_matching_slot_leaves_video_classified_and_waiting(store, synthetic_video, routed_dirs):
    # No slot seeded at all.
    classifier = FakeClassifier(confidence=0.94)

    outcome = process_content.process_one(
        store, FakeTranscriber(), classifier, synthetic_video, dry_run=False
    )

    assert outcome.kind == "WAITING_FOR_SLOT"
    assert outcome.video.status == "CLASSIFIED"
    assert outcome.video.assigned_slot_id is None
    assert synthetic_video.exists()  # not moved — eligible for pickup once a slot opens


def test_rerun_does_not_duplicate_assignment_or_reclassify(store, synthetic_video, routed_dirs):
    """Idempotency: running process_one twice on the same file must not
    retranscribe, reclassify, or double-assign a slot."""
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)
    classifier = FakeClassifier(confidence=0.94)
    transcriber = FakeTranscriber()

    first = process_content.process_one(store, transcriber, classifier, synthetic_video, dry_run=False)
    assert first.kind == "ASSIGNED"
    assert classifier.calls == 1

    # Simulate the file being placed back into incoming/ (e.g. duplicate drop).
    moved_path = processed_dir / "video_001.mp4"
    second = process_content.process_one(store, transcriber, classifier, moved_path, dry_run=False)

    assert second.kind == "ALREADY_ASSIGNED"
    assert classifier.calls == 1  # not called again

    slots = store._conn.execute("SELECT COUNT(*) AS n FROM content_slots WHERE status = 'ASSIGNED'").fetchone()
    assert slots["n"] == 1  # no duplicate slot claim


def test_dry_run_does_not_mutate_slot_or_move_file(store, synthetic_video, routed_dirs):
    processed_dir, _ = routed_dirs
    _seed_open_slot(store)
    classifier = FakeClassifier(confidence=0.94)

    outcome = process_content.process_one(
        store, FakeTranscriber(), classifier, synthetic_video, dry_run=True
    )

    assert outcome.kind == "WOULD_ASSIGN"
    assert synthetic_video.exists()
    assert not (processed_dir / "video_001.mp4").exists()
    open_slot = store.find_earliest_open_slot("building", "1970-01-01T00:00:00")
    assert open_slot is not None  # slot is still OPEN, not claimed

    # Cached transcript/classification should be reused on a real run next.
    rerun_classifier = FakeClassifier(confidence=0.94)
    second = process_content.process_one(
        store, FakeTranscriber(), rerun_classifier, synthetic_video, dry_run=False
    )
    assert second.kind == "ASSIGNED"
    assert rerun_classifier.calls == 0  # classification result was cached from the dry run
