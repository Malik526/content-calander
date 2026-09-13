"""Tests for download_shofo_samples.py: filtering, deterministic stratified
sampling, manifest serialization, and download/verification behavior.

No real Hugging Face network access — datasets.load_dataset and
huggingface_hub.hf_hub_download are always mocked/monkeypatched here."""

import json

import pytest

import download_shofo_samples as dss
import media


def _row(
    video_id="v1", file_name="v1.mp4", has_audio=True, language="en",
    transcript="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
    duration_ms=15_000, has_music=False, **extra,
):
    row = {
        "video_id": video_id, "file_name": file_name, "has_audio": has_audio,
        "language": language, "transcript": transcript, "duration_ms": duration_ms,
        "has_music": has_music, "tiktok_url": f"https://tiktok.com/{video_id}",
        "resolution": "576x1024", "width": 576, "height": 1024, "fps": 30,
        "codec": "h264", "bitrate": 850000,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# is_valid_candidate
# ---------------------------------------------------------------------------

def test_is_valid_candidate_accepts_well_formed_row():
    assert dss.is_valid_candidate(_row()) is True


def test_is_valid_candidate_rejects_no_audio():
    assert dss.is_valid_candidate(_row(has_audio=False)) is False


def test_is_valid_candidate_rejects_non_english():
    assert dss.is_valid_candidate(_row(language="fr")) is False


def test_is_valid_candidate_rejects_missing_file_name():
    assert dss.is_valid_candidate(_row(file_name="")) is False


def test_is_valid_candidate_rejects_empty_transcript():
    assert dss.is_valid_candidate(_row(transcript="")) is False
    assert dss.is_valid_candidate(_row(transcript="   ")) is False


# ---------------------------------------------------------------------------
# bucket_for_duration
# ---------------------------------------------------------------------------

def test_bucket_for_duration_boundaries():
    assert dss.bucket_for_duration(10_000) == "short"
    assert dss.bucket_for_duration(29_999) == "short"
    assert dss.bucket_for_duration(30_000) == "medium"
    assert dss.bucket_for_duration(89_999) == "medium"
    assert dss.bucket_for_duration(90_000) == "long"
    assert dss.bucket_for_duration(300_000) == "long"


def test_bucket_for_duration_handles_missing_value():
    assert dss.bucket_for_duration(None) == "short"


# ---------------------------------------------------------------------------
# stratify_sample
# ---------------------------------------------------------------------------

def _candidates_across_buckets():
    candidates = []
    for i in range(6):
        candidates.append(_row(video_id=f"short_{i}", duration_ms=15_000, has_music=(i % 2 == 0)))
    for i in range(6):
        candidates.append(_row(video_id=f"medium_{i}", duration_ms=45_000, has_music=(i % 2 == 0)))
    for i in range(6):
        candidates.append(_row(video_id=f"long_{i}", duration_ms=120_000, has_music=(i % 2 == 0)))
    return candidates


def test_stratify_sample_spreads_across_duration_buckets():
    selected = dss.stratify_sample(_candidates_across_buckets(), count=12, seed=42)
    assert len(selected) == 12
    buckets = [dss.bucket_for_duration(r["duration_ms"]) for r in selected]
    assert buckets.count("short") == 4
    assert buckets.count("medium") == 4
    assert buckets.count("long") == 4


def test_stratify_sample_is_deterministic_for_same_seed():
    candidates = _candidates_across_buckets()
    first = dss.stratify_sample(candidates, count=12, seed=42)
    second = dss.stratify_sample(candidates, count=12, seed=42)
    assert [r["video_id"] for r in first] == [r["video_id"] for r in second]


def test_stratify_sample_different_seeds_can_differ():
    candidates = _candidates_across_buckets()
    a = dss.stratify_sample(candidates, count=12, seed=1)
    b = dss.stratify_sample(candidates, count=12, seed=2)
    assert [r["video_id"] for r in a] != [r["video_id"] for r in b]


def test_stratify_sample_includes_both_music_values_when_available():
    selected = dss.stratify_sample(_candidates_across_buckets(), count=12, seed=42)
    music_values = {r["has_music"] for r in selected}
    assert music_values == {True, False}


def test_stratify_sample_handles_fewer_candidates_than_requested():
    candidates = [_row(video_id="only_one", duration_ms=15_000)]
    selected = dss.stratify_sample(candidates, count=12, seed=42)
    assert len(selected) == 1


def test_stratify_sample_zero_count_returns_empty():
    assert dss.stratify_sample(_candidates_across_buckets(), count=0, seed=42) == []


def test_stratify_sample_empty_candidates_returns_empty():
    assert dss.stratify_sample([], count=12, seed=42) == []


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def test_build_manifest_record_preserves_transcript_unmodified(tmp_path):
    row = _row(transcript="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRaw text kept as-is.\n")
    output_dir = tmp_path
    local_path = output_dir / "videos" / "v1.mp4"
    record = dss.build_manifest_record(row, 1, local_path, output_dir, "Shofo/shofo-talking-head-en")

    assert record["reference_transcript"] == row["transcript"]
    assert record["sample_index"] == 1
    assert record["video_id"] == "v1"
    assert record["local_path"] == "videos/v1.mp4"
    assert record["dataset"] == "Shofo/shofo-talking-head-en"


def test_write_manifest_writes_one_json_object_per_line(tmp_path):
    records = [
        {"video_id": "a", "reference_transcript": "hello"},
        {"video_id": "b", "reference_transcript": "world"},
    ]
    manifest_path = tmp_path / "metadata.jsonl"
    dss.write_manifest(records, manifest_path)

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["video_id"] == "a"
    assert json.loads(lines[1])["video_id"] == "b"


# ---------------------------------------------------------------------------
# iter_dataset_rows — dataset access errors (mocked, no real network)
# ---------------------------------------------------------------------------

def test_iter_dataset_rows_wraps_upstream_errors_actionably(monkeypatch):
    def fake_load_dataset(*args, **kwargs):
        raise RuntimeError("403 Client Error: gated dataset")

    monkeypatch.setattr(dss, "load_dataset", fake_load_dataset)

    with pytest.raises(dss.DatasetAccessError) as exc_info:
        list(dss.iter_dataset_rows("Shofo/shofo-talking-head-en"))

    message = str(exc_info.value)
    assert "huggingface-cli login" in message
    assert "HF_TOKEN" in message
    assert "accept the dataset's access conditions" in message


def test_iter_dataset_rows_missing_datasets_package_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(dss, "load_dataset", None)
    with pytest.raises(dss.DatasetAccessError) as exc_info:
        list(dss.iter_dataset_rows("Shofo/shofo-talking-head-en"))
    assert "requirements-eval.txt" in str(exc_info.value)


class _FakeStreamingDataset:
    def __init__(self, rows, features):
        self._rows = rows
        self.features = features

    def remove_columns(self, columns):
        remaining_rows = [{k: v for k, v in row.items() if k not in columns} for row in self._rows]
        remaining_features = {k: v for k, v in self.features.items() if k not in columns}
        return _FakeStreamingDataset(remaining_rows, remaining_features)

    def __iter__(self):
        return iter(self._rows)


def test_iter_dataset_rows_drops_video_column_before_iterating(monkeypatch):
    rows = [{"video": b"binary-bytes-should-not-appear", "video_id": "v1", "language": "en"}]
    fake_ds = _FakeStreamingDataset(rows, features={"video": object(), "video_id": object(), "language": object()})
    monkeypatch.setattr(dss, "load_dataset", lambda *a, **k: fake_ds)

    result = list(dss.iter_dataset_rows("Shofo/shofo-talking-head-en"))

    assert result == [{"video_id": "v1", "language": "en"}]
    assert "video" not in result[0]


# ---------------------------------------------------------------------------
# download_sample — mocked huggingface_hub, real ffprobe verification
# ---------------------------------------------------------------------------

def _write_valid_mp4(path):
    # Reuse the project's existing ffmpeg-synthesis helper pattern: a tiny
    # real video is more useful here than an arbitrary byte blob, since
    # download_sample optionally runs real ffprobe verification.
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, timeout=30,
    )


@pytest.fixture
def ffmpeg_available():
    try:
        media.check_ffmpeg_available()
    except media.FfmpegNotFoundError:
        pytest.skip("ffmpeg/ffprobe not available")


def test_download_sample_skips_existing_nonempty_file(tmp_path, monkeypatch):
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    target = videos_dir / "v1.mp4"
    target.write_bytes(b"already here")

    def fail_if_called(*a, **k):
        raise AssertionError("hf_hub_download should not be called for an existing file")

    monkeypatch.setattr(dss, "hf_hub_download", fail_if_called)

    result = dss.download_sample(_row(), 1, "Shofo/shofo-talking-head-en", videos_dir, verify_media=False)

    assert result.success is True
    assert result.local_path == target


def test_download_sample_reports_failure_without_raising(tmp_path, monkeypatch):
    videos_dir = tmp_path / "videos"

    def fake_download(*a, **k):
        raise RuntimeError("network error")

    monkeypatch.setattr(dss, "hf_hub_download", fake_download)

    result = dss.download_sample(_row(video_id="bad"), 1, "Shofo/shofo-talking-head-en", videos_dir, verify_media=False)

    assert result.success is False
    assert "network error" in result.error
    assert not (videos_dir / "bad.mp4").exists()


def test_download_sample_missing_huggingface_hub_package(tmp_path, monkeypatch):
    monkeypatch.setattr(dss, "hf_hub_download", None)
    result = dss.download_sample(_row(), 1, "Shofo/shofo-talking-head-en", tmp_path / "videos", verify_media=False)
    assert result.success is False
    assert "requirements-eval.txt" in result.error


def test_download_sample_copies_and_verifies_real_media(tmp_path, monkeypatch, ffmpeg_available):
    cached = tmp_path / "hf_cache" / "v1.mp4"
    _write_valid_mp4(cached)

    monkeypatch.setattr(dss, "hf_hub_download", lambda **kwargs: str(cached))

    videos_dir = tmp_path / "videos"
    result = dss.download_sample(_row(), 1, "Shofo/shofo-talking-head-en", videos_dir, verify_media=True)

    assert result.success is True
    assert result.local_path.exists()
    assert result.file_size_bytes > 0


def test_download_sample_verification_rejects_corrupt_file(tmp_path, monkeypatch, ffmpeg_available):
    cached = tmp_path / "hf_cache" / "v1.mp4"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"not a real video file")

    monkeypatch.setattr(dss, "hf_hub_download", lambda **kwargs: str(cached))

    videos_dir = tmp_path / "videos"
    result = dss.download_sample(_row(), 1, "Shofo/shofo-talking-head-en", videos_dir, verify_media=True)

    assert result.success is False
    assert "media inspection failed" in result.error
    assert not (videos_dir / "v1.mp4").exists()


def test_download_sample_empty_downloaded_file_is_a_failure(tmp_path, monkeypatch):
    cached = tmp_path / "hf_cache" / "v1.mp4"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"")

    monkeypatch.setattr(dss, "hf_hub_download", lambda **kwargs: str(cached))

    result = dss.download_sample(_row(), 1, "Shofo/shofo-talking-head-en", tmp_path / "videos", verify_media=False)

    assert result.success is False
    assert "empty" in result.error
