"""Unit tests for media.py, mocking ffprobe so no system binary is required."""

import json
import subprocess
from types import SimpleNamespace

import pytest

import media


def _ffprobe_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _probe_json(*, video_codec="h264", audio_codec="aac", has_audio=True, width=1080, height=1920, fps="30/1", duration="72.4", size="48102933"):
    streams = [
        {"codec_type": "video", "codec_name": video_codec, "width": width, "height": height, "r_frame_rate": fps},
    ]
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": audio_codec})
    return json.dumps({"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration, "size": size}, "streams": streams})


def test_inspect_media_mp4_h264(monkeypatch, tmp_path):
    path = tmp_path / "video_002.mp4"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(video_codec="h264")))

    info = media.inspect_media(path)

    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.width == 1080 and info.height == 1920
    assert info.fps == 30.0
    assert info.duration_seconds == 72.4


def test_inspect_media_mov_h264(monkeypatch, tmp_path):
    path = tmp_path / "video.mov"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(video_codec="h264")))

    info = media.inspect_media(path)

    assert info.container.startswith("mov")
    assert info.video_codec == "h264"


def test_inspect_media_mov_hevc(monkeypatch, tmp_path):
    """iPhone 'High Efficiency' recordings: MOV container, HEVC video, AAC audio."""
    path = tmp_path / "iphone_video.mov"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(video_codec="hevc")))

    info = media.inspect_media(path)

    assert info.video_codec == "hevc"
    tiktok_ok, _ = media.is_tiktok_compatible(info)
    assert tiktok_ok is True


def test_inspect_media_no_audio_stream(monkeypatch, tmp_path):
    path = tmp_path / "silent.mp4"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(has_audio=False)))

    with pytest.raises(media.NoAudioStreamError):
        media.inspect_media(path)


def test_inspect_media_corrupt_file(monkeypatch, tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"not a real video")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(returncode=1, stderr="Invalid data found"))

    with pytest.raises(media.CorruptMediaError):
        media.inspect_media(path)


def test_inspect_media_unsupported_codec(monkeypatch, tmp_path):
    """ffprobe finds a stream but cannot name a codec for it."""
    path = tmp_path / "exotic.mov"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(video_codec="")))

    with pytest.raises(media.UnsupportedCodecError):
        media.inspect_media(path)


def test_is_tiktok_compatible_rejects_unknown_video_codec(monkeypatch, tmp_path):
    path = tmp_path / "video.mov"
    path.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ffprobe_result(stdout=_probe_json(video_codec="prores")))

    info = media.inspect_media(path)
    ok, reason = media.is_tiktok_compatible(info)

    assert ok is False
    assert "prores" in reason


def test_check_ffmpeg_available_raises_when_missing(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda tool: None)

    with pytest.raises(media.FfmpegNotFoundError):
        media.check_ffmpeg_available()


def test_file_hash_is_stable(tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"identical content")
    other = tmp_path / "b.mp4"
    other.write_bytes(b"identical content")

    assert media.file_hash(path) == media.file_hash(other)

    different = tmp_path / "c.mp4"
    different.write_bytes(b"different content")
    assert media.file_hash(path) != media.file_hash(different)
