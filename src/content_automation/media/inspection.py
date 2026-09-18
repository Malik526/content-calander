"""
media.py — Media inspection and audio extraction for process_content.py.

What it does:
  Inspects video files with ffprobe (container, codecs, dimensions, duration)
  without decoding or transcoding them, and extracts a lightweight mono WAV
  audio derivative for transcription. The original video is never modified.

Principle (see docs/decisions/0001-video-ingestion-pipeline.md):
  Pass through when compatible, transcode only when required. V1 never
  transcodes video — it only ever extracts a small audio derivative.

TikTok compatibility (`is_tiktok_compatible`) is generic informational
validation only; no publishing happens in this milestone. It is kept as a
separate function from the generic `inspect_media` check so platform-specific
rules never leak into generic ingestion validation.

Dependencies:
  ffmpeg + ffprobe on PATH (system binaries, not a Python dependency).
"""

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from content_automation.config import TIKTOK_CONTAINERS, TIKTOK_VIDEO_CODECS


class MediaError(Exception):
    """Base class for media inspection/extraction failures.

    `reason_code` matches the failure taxonomy used by videos.failure_reason.
    """

    def __init__(self, message: str, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code


class CorruptMediaError(MediaError):
    def __init__(self, message: str):
        super().__init__(message, "CORRUPT_MEDIA")


class NoAudioStreamError(MediaError):
    def __init__(self, message: str):
        super().__init__(message, "NO_AUDIO_STREAM")


class UnsupportedCodecError(MediaError):
    def __init__(self, message: str):
        super().__init__(message, "UNSUPPORTED_CODEC")


class FfmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe are not on PATH. Not a per-video failure."""


@dataclass
class MediaInfo:
    path: Path
    container: str
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration_seconds: float | None
    file_size_bytes: int


def check_ffmpeg_available() -> None:
    """Fail fast and clearly if ffmpeg/ffprobe are not installed.

    process_content.py calls this once, before touching any video, per the
    project decision to treat ffmpeg as an explicit system prerequisite
    rather than pretending it is a Python dependency.
    """
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FfmpegNotFoundError(
            "Missing required system binaries: " + ", ".join(missing) + ".\n"
            "Install ffmpeg (which provides both ffmpeg and ffprobe), e.g.:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt-get install ffmpeg\n"
            "Then re-run process_content.py."
        )


def file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Stable content identity used for idempotency (sha256 of file bytes)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_media(path: Path) -> MediaInfo:
    """Run ffprobe against `path` and return structured MediaInfo.

    Raises CorruptMediaError if ffprobe cannot parse the file, NoAudioStreamError
    if there is no audio stream, UnsupportedCodecError if ffprobe cannot name a
    video or audio codec for an existing stream.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise CorruptMediaError(f"ffprobe timed out inspecting {path}") from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise CorruptMediaError(
            f"ffprobe could not read {path}: {result.stderr.strip() or 'no output'}"
        )

    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CorruptMediaError(f"ffprobe returned invalid JSON for {path}") from exc

    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise CorruptMediaError(f"{path} has no video stream")
    if audio_stream is None:
        raise NoAudioStreamError(f"{path} has no audio stream")

    video_codec = video_stream.get("codec_name")
    audio_codec = audio_stream.get("codec_name")
    if not video_codec or not audio_codec:
        raise UnsupportedCodecError(
            f"{path} has a stream ffprobe could not name a codec for "
            f"(video={video_codec}, audio={audio_codec})"
        )

    container = (fmt.get("format_name") or "").split(",")[0] or path.suffix.lstrip(".").lower()

    return MediaInfo(
        path=path,
        container=container,
        video_codec=video_codec,
        audio_codec=audio_codec,
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        fps=_parse_frame_rate(video_stream.get("r_frame_rate")),
        duration_seconds=_safe_float(fmt.get("duration")),
        file_size_bytes=int(fmt.get("size") or path.stat().st_size),
    )


def is_tiktok_compatible(info: MediaInfo) -> tuple[bool, str]:
    """Informational TikTok container/codec check. Does not block ingestion in V1."""
    container_key = info.container.lower()
    if info.path.suffix.lower() == ".mov" and "mov" not in container_key:
        container_key = "mov"

    if container_key not in TIKTOK_CONTAINERS:
        return False, f"container '{info.container}' is not one of {sorted(TIKTOK_CONTAINERS)}"
    if info.video_codec not in TIKTOK_VIDEO_CODECS:
        return False, f"video codec '{info.video_codec}' is not one of {sorted(TIKTOK_VIDEO_CODECS)}"
    return True, "compatible"


def extract_audio(info: MediaInfo, out_dir: Path | None = None) -> Path:
    """Extract a mono 16kHz WAV derivative for transcription.

    Only the audio stream is decoded — the original video is never
    re-encoded. Caller is responsible for cleanup (see cleanup_audio).
    """
    out_dir = out_dir or Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{info.path.stem}.{file_hash(info.path)[:12]}.wav"

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", str(info.path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0 or not out_path.exists():
        raise CorruptMediaError(
            f"ffmpeg failed to extract audio from {info.path}: {result.stderr.strip()}"
        )
    return out_path


def cleanup_audio(audio_path: Path) -> None:
    audio_path.unlink(missing_ok=True)


def _parse_frame_rate(raw: str | None) -> float | None:
    if not raw or "/" not in raw:
        return _safe_float(raw)
    num, _, den = raw.partition("/")
    try:
        num_f, den_f = float(num), float(den)
        return round(num_f / den_f, 3) if den_f else None
    except ValueError:
        return None


def _safe_float(raw) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None
