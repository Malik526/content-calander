"""Tests for evaluate_transcription.py: manifest loading, per-clip
evaluation, and aggregate metrics. media.py and the transcriber are always
faked/monkeypatched — no real ffmpeg or faster-whisper model download
required, and no dependency on the (gitignored, real) Shofo corpus."""

import json

import pytest

import evaluate_transcription as et
from content_automation.media import inspection as media
from content_automation.media import transcription


class FakeTranscriber:
    def __init__(self, text="hello world", raises=None):
        self.text = text
        self.raises = raises

    def transcribe(self, audio_path):
        if self.raises:
            raise self.raises
        return transcription.TranscriptResult(text=self.text, language="en", duration_seconds=1.0)


def _manifest_record(**overrides):
    defaults = dict(
        sample_index=1, video_id="v1", local_path="videos/v1.mp4",
        reference_transcript="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello world\n",
        has_music=False, duration_ms=10_000,
    )
    defaults.update(overrides)
    return et.ManifestRecord(**defaults)


def _fake_media_info(duration=10.0):
    return media.MediaInfo(
        path=None, container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=duration, file_size_bytes=1000,
    )


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------

def test_load_manifest_reads_jsonl(tmp_path):
    manifest = tmp_path / "metadata.jsonl"
    manifest.write_text(
        json.dumps({
            "sample_index": 1, "video_id": "v1", "local_path": "videos/v1.mp4",
            "reference_transcript": "hi", "has_music": True, "duration_ms": 12000,
        }) + "\n"
    )
    records = et.load_manifest(manifest)
    assert len(records) == 1
    assert records[0].video_id == "v1"
    assert records[0].has_music is True


def test_load_manifest_missing_file_raises_actionable_error(tmp_path):
    with pytest.raises(et.ManifestError) as exc_info:
        et.load_manifest(tmp_path / "does_not_exist.jsonl")
    assert "download_shofo_samples.py" in str(exc_info.value)


def test_load_manifest_rejects_empty_file(tmp_path):
    manifest = tmp_path / "metadata.jsonl"
    manifest.write_text("")
    with pytest.raises(et.ManifestError):
        et.load_manifest(manifest)


def test_load_manifest_handles_missing_reference_transcript(tmp_path):
    manifest = tmp_path / "metadata.jsonl"
    manifest.write_text(json.dumps({"sample_index": 1, "video_id": "v1", "local_path": "videos/v1.mp4"}) + "\n")
    records = et.load_manifest(manifest)
    assert records[0].reference_transcript == ""


# ---------------------------------------------------------------------------
# evaluate_one
# ---------------------------------------------------------------------------

def test_evaluate_one_computes_wer_and_realtime_factor(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "inspect_media", lambda path: _fake_media_info(duration=10.0))
    monkeypatch.setattr(media, "extract_audio", lambda info: tmp_path / "audio.wav")
    monkeypatch.setattr(media, "cleanup_audio", lambda path: None)

    record = _manifest_record()
    outcome = et.evaluate_one(record, FakeTranscriber(text="hello world"), tmp_path)

    assert outcome.wer == 0.0
    assert outcome.cer == 0.0
    assert outcome.error is None
    assert outcome.video_duration_seconds == 10.0
    assert outcome.realtime_factor is not None and outcome.realtime_factor >= 0


def test_evaluate_one_handles_transcription_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "inspect_media", lambda path: _fake_media_info())
    monkeypatch.setattr(media, "extract_audio", lambda info: tmp_path / "audio.wav")
    monkeypatch.setattr(media, "cleanup_audio", lambda path: None)

    record = _manifest_record()
    outcome = et.evaluate_one(record, FakeTranscriber(raises=transcription.TranscriptionError("boom")), tmp_path)

    assert outcome.error is not None
    assert outcome.wer is None
    assert outcome.cer is None
    assert outcome.realtime_factor is None


def test_evaluate_one_handles_media_inspection_failure(tmp_path, monkeypatch):
    def raise_media_error(path):
        raise media.CorruptMediaError("corrupt")

    monkeypatch.setattr(media, "inspect_media", raise_media_error)

    record = _manifest_record()
    outcome = et.evaluate_one(record, FakeTranscriber(), tmp_path)

    assert outcome.error is not None
    assert "media inspection failed" in outcome.error
    assert outcome.wer is None


def test_evaluate_one_handles_empty_reference_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "inspect_media", lambda path: _fake_media_info())
    monkeypatch.setattr(media, "extract_audio", lambda info: tmp_path / "audio.wav")
    monkeypatch.setattr(media, "cleanup_audio", lambda path: None)

    record = _manifest_record(reference_transcript="")
    outcome = et.evaluate_one(record, FakeTranscriber(text="hello"), tmp_path)

    assert outcome.wer is None
    assert outcome.cer is None
    assert outcome.error is None  # transcription itself succeeded; only WER is unscoreable


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def _outcome(video_id, wer, has_music=False, error=None):
    return et.TranscriptionOutcome(
        video_id=video_id, reference_transcript="ref", predicted_transcript="pred",
        wer=wer, cer=wer, transcription_seconds=1.0, video_duration_seconds=10.0,
        realtime_factor=0.1, has_music=has_music, error=error,
    )


def test_summarize_computes_mean_median_best_worst():
    outcomes = [_outcome("a", 0.1), _outcome("b", 0.5), _outcome("c", 0.3)]
    metrics = et.summarize(outcomes)

    assert metrics.total == 3
    assert metrics.scored == 3
    assert metrics.errors == 0
    assert metrics.mean_wer == pytest.approx(0.3)
    assert metrics.median_wer == pytest.approx(0.3)
    assert metrics.best.video_id == "a"
    assert metrics.worst.video_id == "b"


def test_summarize_excludes_unscoreable_and_error_outcomes():
    outcomes = [
        _outcome("a", 0.2),
        _outcome("b", None),  # empty reference, not scoreable
        _outcome("c", None, error="failed"),
    ]
    metrics = et.summarize(outcomes)
    assert metrics.total == 3
    assert metrics.scored == 1
    assert metrics.errors == 1
    assert metrics.mean_wer == pytest.approx(0.2)


def test_summarize_breaks_down_wer_by_has_music():
    outcomes = [
        _outcome("a", 0.1, has_music=True),
        _outcome("b", 0.3, has_music=True),
        _outcome("c", 0.2, has_music=False),
    ]
    metrics = et.summarize(outcomes)
    assert metrics.mean_wer_with_music == pytest.approx(0.2)
    assert metrics.mean_wer_without_music == pytest.approx(0.2)


def test_summarize_handles_no_scoreable_outcomes():
    metrics = et.summarize([_outcome("a", None, error="failed")])
    assert metrics.mean_wer is None
    assert metrics.best is None
    assert metrics.worst is None


# ---------------------------------------------------------------------------
# write_results
# ---------------------------------------------------------------------------

def test_write_results_writes_jsonl(tmp_path):
    outcomes = [_outcome("a", 0.1), _outcome("b", 0.2)]
    results_path = tmp_path / "results.jsonl"
    et.write_results(outcomes, results_path)

    lines = results_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["video_id"] == "a"
