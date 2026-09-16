"""
publisher.py — Publisher interface for pushing a local video to an external
platform, and PublishError/result types shared by every implementation.

What it does:
  Defines a platform-neutral interface (Publisher) so publish_tiktok.py (and
  any future per-platform CLI) never depends on a specific platform's API
  shape. Milestone 2.0 ships exactly one implementation, TikTokPublisher
  (tiktok_publisher.py) — see
  docs/decisions/0006-tiktok-publisher-foundation.md. Mirrors the existing
  Transcriber (transcription.py) / ContentClassifier (classification.py)
  interface-plus-factory pattern already used in this codebase.

  No I/O of its own — this module only defines contracts. Every
  platform-specific request format, endpoint, token, and response-parsing
  detail lives inside that platform's own adapter module, never here and
  never in process_content.py/publish_tiktok.py.

Dependencies:
  stdlib only.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class PublishError(Exception):
    """Raised when a publish or status-check call fails for any reason:
    missing local file, missing/expired credentials, a malformed platform
    response, an HTTP-level failure, or the platform reporting its own
    error. `reason_code` is a short machine-stable label (e.g.
    "LOCAL_FILE_MISSING", "MALFORMED_RESPONSE", "UPLOAD_FAILED", or a
    platform-reported error code) so callers can persist/report it without
    parsing the message string."""

    def __init__(self, message: str, reason_code: str = "PUBLISH_FAILED"):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass
class PublishResult:
    """Returned by Publisher.publish() once a video has been submitted.
    `status` is the platform's own reported state immediately after
    submission (e.g. TikTok's "PROCESSING_UPLOAD") — publishing is
    asynchronous, so this is rarely a terminal state; call get_status()
    later to find out what actually happened."""
    platform_post_id: str
    status: str
    raw_response: dict | None = None


@dataclass
class PublishStatusResult:
    """Returned by Publisher.get_status(). `status` is the platform's own
    vocabulary (not normalized to this project's platform_posts.status
    values) — callers map it to PENDING/PUBLISHING/PUBLISHED/FAILED
    themselves, since that mapping is platform-specific."""
    status: str
    failure_reason: str | None = None
    raw_response: dict | None = None


class Publisher(ABC):
    @abstractmethod
    def publish(self, video_path: Path, caption: str) -> PublishResult:
        """Upload video_path and submit it for publishing with caption.
        Raises PublishError on any failure — missing file, auth, upload,
        or a malformed/error platform response. Must not silently retry an
        ambiguous result as a brand-new submission; that policy lives in
        the caller (publish_tiktok.py), which owns idempotency via
        content_store.get_platform_post()."""

    @abstractmethod
    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        """Check a previously submitted post's current status. Raises
        PublishError on a malformed response or platform-reported error —
        never returns a fabricated status just to avoid raising."""


class UnsupportedPlatformError(Exception):
    """Raised by build_publisher() for a platform with no registered implementation."""


def build_publisher(platform: str, **kwargs) -> Publisher:
    """Construct the Publisher for `platform`. Only "tiktok" exists as of
    Milestone 2.0 — deliberately not pre-building an Instagram/YouTube stub,
    per that milestone's explicit scope. Raises UnsupportedPlatformError
    immediately and clearly on anything else, mirroring
    classification.build_classifier()'s unsupported-value message style."""
    if platform == "tiktok":
        from tiktok_publisher import TikTokPublisher
        return TikTokPublisher(**kwargs)
    raise UnsupportedPlatformError(f"Unsupported platform={platform!r}. Valid values: tiktok")
