"""Tests for download_shofo_samples.py: filtering, deterministic stratified
sampling, manifest serialization, and download/verification behavior.

No real Hugging Face network access — datasets.load_dataset and
huggingface_hub.hf_hub_download are always mocked/monkeypatched here."""

import json

import pytest

import download_shofo_samples as dss
from content_automation.media import inspection as media


DATASET_NAME = "Shofo/shofo-talking-head-en"


def _row(
    video_id="v1", file_name="videos/70/v1.mp4", has_audio=True, language="en",
    reference_transcript="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello.\n",
    duration_ms=15_000, has_music=False, **extra,
):
    """A normalized row — i.e. normalize_row()'s output shape — for testing
    everything downstream of the acquisition boundary (filtering,
    stratification, download, manifest). See _raw_shofo_row() below for the
    real, pre-translation Shofo schema."""
    row = {
        "video_id": video_id, "file_name": file_name, "has_audio": has_audio,
        "language": language, "reference_transcript": reference_transcript, "duration_ms": duration_ms,
        "has_music": has_music, "tiktok_url": f"https://www.tiktok.com/@i/video/{video_id}",
        "resolution": "576x1024", "width": 576, "height": 1024, "fps": 30.0,
        "codec": "h264", "bitrate": 850000,
    }
    row.update(extra)
    return row


def _raw_shofo_row(
    video_id="7084695981018582315", has_audio=True, language="en",
    transcript="WEBVTT\n\n00:00:00.140 --> 00:00:00.200\nHey,\n",
    duration_ms=20_501, has_music=False, video_path=None, dataset_name=DATASET_NAME, **extra,
):
    """A row shaped exactly like a real streamed Shofo row (verified
    empirically 2026-09-13, decoding disabled) — see
    evaluation/video_pipeline/README.md "Observed Schema". Notably: no
    "file_name" column at all; the downloadable reference lives inside the
    raw, non-decoded "video" feature value instead."""
    if video_path is None:
        video_path = f"hf://datasets/{dataset_name}@1eadfc5e47590f3fb67ec3f01376b6ed81c24016/videos/70/{video_id}.mp4"
    row = {
        "video_id": video_id, "has_audio": has_audio, "language": language,
        "transcript": transcript, "duration_ms": duration_ms, "has_music": has_music,
        "tiktok_url": f"https://www.tiktok.com/@i/video/{video_id}",
        "resolution": "576x1024", "width": 576, "height": 1024, "fps": 30.0,
        "codec": "h264", "bitrate": 1449790,
        "video": {"path": video_path, "bytes": None} if video_path is not False else None,
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
    assert dss.is_valid_candidate(_row(reference_transcript="")) is False
    assert dss.is_valid_candidate(_row(reference_transcript="   ")) is False


def test_is_valid_candidate_rejects_missing_field_distinctly_from_falsy_value():
    """A field that's genuinely absent (None, via normalize_row on a raw
    row lacking it) must be rejected exactly like a validly-present falsy
    value — neither should accidentally pass."""
    missing_audio = _row()
    del missing_audio["has_audio"]
    assert dss.is_valid_candidate(missing_audio) is False  # KeyError-safe via .get()
    assert dss.is_valid_candidate(_row(has_audio=None)) is False
    assert dss.is_valid_candidate(_row(has_audio=False)) is False


def test_is_valid_candidate_does_not_loosen_on_a_falsy_but_valid_bitrate():
    """A real, valid 0 (or otherwise falsy) metadata value on a field that
    isn't itself a required filter must never affect candidacy."""
    assert dss.is_valid_candidate(_row(bitrate=0)) is True
    assert dss.is_valid_candidate(_row(has_music=False)) is True


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
    row = _row(reference_transcript="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRaw text kept as-is.\n")
    output_dir = tmp_path
    local_path = output_dir / "videos" / "v1.mp4"
    record = dss.build_manifest_record(row, 1, local_path, output_dir, DATASET_NAME)

    assert record["reference_transcript"] == row["reference_transcript"]
    assert record["file_name"] == row["file_name"]
    assert record["sample_index"] == 1
    assert record["video_id"] == "v1"
    assert record["local_path"] == "videos/v1.mp4"
    assert record["dataset"] == DATASET_NAME


# ---------------------------------------------------------------------------
# normalize_row / _relative_path_from_video_field — the real acquisition
# boundary between Shofo's actual schema and our internal field names
# ---------------------------------------------------------------------------

def test_relative_path_from_video_field_parses_hf_dataset_uri():
    video_field = {"path": f"hf://datasets/{DATASET_NAME}@abc123/videos/70/7084695981018582315.mp4", "bytes": None}
    assert dss._relative_path_from_video_field(video_field, DATASET_NAME) == "videos/70/7084695981018582315.mp4"


def test_relative_path_from_video_field_rejects_mismatched_repo_id():
    video_field = {"path": "hf://datasets/someone-else/other-dataset@abc123/videos/x.mp4", "bytes": None}
    assert dss._relative_path_from_video_field(video_field, DATASET_NAME) is None


def test_relative_path_from_video_field_passes_through_plain_relative_path():
    video_field = {"path": "videos/70/x.mp4", "bytes": None}
    assert dss._relative_path_from_video_field(video_field, DATASET_NAME) == "videos/70/x.mp4"


def test_relative_path_from_video_field_handles_missing_or_malformed_input():
    assert dss._relative_path_from_video_field(None, DATASET_NAME) is None
    assert dss._relative_path_from_video_field({}, DATASET_NAME) is None
    assert dss._relative_path_from_video_field({"path": None, "bytes": None}, DATASET_NAME) is None
    assert dss._relative_path_from_video_field({"path": "", "bytes": None}, DATASET_NAME) is None
    assert dss._relative_path_from_video_field("not-a-dict", DATASET_NAME) is None


def test_normalize_row_maps_real_schema_to_internal_names():
    raw = _raw_shofo_row(video_id="v1", has_music=True, duration_ms=42_000)
    normalized = dss.normalize_row(raw, DATASET_NAME)

    assert normalized["video_id"] == "v1"
    assert normalized["file_name"] == "videos/70/v1.mp4"
    assert normalized["reference_transcript"] == raw["transcript"]
    assert normalized["duration_ms"] == 42_000
    assert normalized["has_music"] is True
    assert normalized["has_audio"] is True
    assert normalized["language"] == "en"
    assert normalized["resolution"] == "576x1024"
    assert normalized["width"] == 576
    assert normalized["height"] == 1024
    assert normalized["fps"] == 30.0
    assert normalized["codec"] == "h264"
    assert normalized["bitrate"] == 1449790
    assert normalized["tiktok_url"] == raw["tiktok_url"]


def test_normalize_row_missing_video_field_yields_none_file_name():
    raw = _raw_shofo_row(video_path=False)  # no "video" key at all
    normalized = dss.normalize_row(raw, DATASET_NAME)
    assert normalized["file_name"] is None
    assert dss.is_valid_candidate(normalized) is False  # correctly filtered, not a crash


def test_normalize_row_output_is_a_valid_candidate_for_a_well_formed_real_row():
    raw = _raw_shofo_row()
    normalized = dss.normalize_row(raw, DATASET_NAME)
    assert dss.is_valid_candidate(normalized) is True


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


class _VideoDecodeError(Exception):
    """Stands in for the real error this fixes: `datasets` raising
    "To support decoding videos, please install torchcodec." whenever a
    row's "video" feature is actually decoded."""


class _FakeStreamingDataset:
    """Models the real failure mode precisely: iterating a row containing
    a "video" key raises _VideoDecodeError *unless* decoding has been
    disabled first (.decode(False) or the Video(decode=False) cast_column
    fallback) — matching the real Video feature's behavior. The "video"
    column itself is never removed by the fix under test (it's needed
    downstream to derive file_name — see normalize_row), only its decoding
    is disabled. Deliberately has no `.features` property: the fix must
    not depend on accessing it (that access is what caused the original
    bug — see _disable_video_decoding's docstring)."""

    def __init__(self, rows, decode_enabled=True, has_video_column=True):
        self._rows = rows
        self._decode_enabled = decode_enabled
        self._has_video_column = has_video_column

    def decode(self, enable):
        return _FakeStreamingDataset(self._rows, decode_enabled=enable, has_video_column=self._has_video_column)

    def cast_column(self, name, feature):
        # Real cast_column raises ValueError for an unknown column name.
        if name == "video" and not self._has_video_column:
            raise ValueError(f"Column {name} not in the dataset")
        return _FakeStreamingDataset(self._rows, decode_enabled=False, has_video_column=self._has_video_column)

    def __iter__(self):
        for row in self._rows:
            if "video" in row and self._decode_enabled:
                raise _VideoDecodeError("To support decoding videos, please install torchcodec.")
            yield row


def test_iter_dataset_rows_never_decodes_video(monkeypatch):
    """The core regression test: iterating must succeed and yield rows
    that still carry the raw (non-decoded) "video" value — needed
    downstream to derive file_name — even though the fake dataset would
    raise the real torchcodec error the moment that value is decoded."""
    rows = [{"video": "would raise if decoded", "video_id": "v1", "language": "en"}]
    fake_ds = _FakeStreamingDataset(rows)
    monkeypatch.setattr(dss, "load_dataset", lambda *a, **k: fake_ds)

    result = list(dss.iter_dataset_rows(DATASET_NAME))

    assert result == [{"video": "would raise if decoded", "video_id": "v1", "language": "en"}]


def test_disable_video_decoding_prefers_decode_method_when_available():
    fake_ds = _FakeStreamingDataset([{"video": "x", "video_id": "v1"}])
    result_ds = dss._disable_video_decoding(fake_ds)
    assert list(result_ds) == [{"video": "x", "video_id": "v1"}]


def test_disable_video_decoding_falls_back_to_video_cast_without_decode_method(monkeypatch):
    """Older `datasets` versions lack IterableDataset.decode() entirely —
    confirm the Video(decode=False) cast_column fallback is used instead,
    and that it alone is enough to stop decoding (the "video" column stays
    present, just no longer decoded)."""

    # hasattr(ds, "decode") must be False for the fallback path to trigger,
    # so build a dataset class with everything _FakeStreamingDataset has
    # except that one method, rather than making it raise (raising would
    # still leave hasattr() True).
    no_decode_cls = type(
        "NoDecodeStreamingDataset", (),
        {k: v for k, v in _FakeStreamingDataset.__dict__.items() if k != "decode"},
    )
    fake_ds = no_decode_cls([{"video": "x", "video_id": "v1"}], decode_enabled=True, has_video_column=True)
    assert not hasattr(fake_ds, "decode")

    fake_video_cls = type("FakeVideo", (), {"__init__": lambda self, decode: None})
    monkeypatch.setattr(dss, "_HfVideo", fake_video_cls)

    result_ds = dss._disable_video_decoding(fake_ds)
    assert list(result_ds) == [{"video": "x", "video_id": "v1"}]


def test_disable_video_decoding_handles_dataset_with_no_video_column():
    fake_ds = _FakeStreamingDataset([{"video_id": "v1", "language": "en"}], has_video_column=False)
    result_ds = dss._disable_video_decoding(fake_ds)
    assert list(result_ds) == [{"video_id": "v1", "language": "en"}]


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
