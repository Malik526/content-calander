"""
tiktok_publisher.py — TikTokPublisher: publishes one local MP4 to TikTok via
the Content Posting API v2 (Direct Post, FILE_UPLOAD source).

What it does:
  Implements publisher.Publisher against TikTok's real, documented Content
  Posting API v2 shape: query the account's creator/privacy/duration
  capabilities, initialize an upload, PUT the raw file bytes to the
  returned upload URL, and poll publish status. See
  docs/decisions/0006-tiktok-publisher-foundation.md for why this exact flow
  (FILE_UPLOAD, not PULL_FROM_URL — no object storage exists yet) and for
  the explicit caveat that the exact request/response shapes here are
  implemented from TikTok's public API documentation, not verified against
  a live call in this environment (no TikTok developer credentials were
  available here) — the one live integration test is the user's manual
  step.

  All TikTok-specific request formats, endpoints, and response parsing live
  here — never in publish_tiktok.py or process_content.py, which only see
  the platform-neutral Publisher interface.

  Unaudited-client privacy restriction: this app has not completed TikTok's
  app review, so Direct Post is restricted to SELF_ONLY (private) posts
  regardless of what other privacy_level_options an account's creator_info
  reports. TikTokPublisher(unaudited=True) — the default, and the only mode
  this milestone exercises — requires SELF_ONLY specifically rather than
  accepting or silently falling back to any other available level; a future
  audited client would pass unaudited=False to use the prior
  requested-level-must-be-offered check instead.

  Caption length: TikTok's Direct Post caption limit is defined in UTF-16
  code units (2200), not Python characters — publish() measures with that
  in mind (see _utf16_length) and fails clearly (CAPTION_TOO_LONG) rather
  than silently truncating the canonical videos.caption_text, which stays
  untouched either way.

  Duration: reuses the existing local media inspection (media.inspect_media
  — the same ffprobe-based function process_content.py uses) rather than
  introducing a second media path, and rejects a video exceeding the
  account's own reported max_video_post_duration_sec before ever calling
  init.

Dependencies:
  requests. media.py for duration/inspection. tiktok_auth.py for access
  tokens. config.py for API base/default privacy level/caption limit.
"""

from pathlib import Path

import requests

import media
from config import TIKTOK_API_BASE, TIKTOK_DEFAULT_PRIVACY_LEVEL, TIKTOK_MAX_CAPTION_UTF16_UNITS
from publisher import PublishError, PublishResult, PublishStatusResult, Publisher
from tiktok_auth import TikTokAuthError, get_access_token

CREATOR_INFO_URL = f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/"
INIT_URL = f"{TIKTOK_API_BASE}/v2/post/publish/video/init/"
STATUS_URL = f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/"

_REQUEST_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 300

_UNAUDITED_REQUIRED_PRIVACY_LEVEL = "SELF_ONLY"


def _utf16_length(text: str) -> int:
    """Count UTF-16 code units the way TikTok's 2200-unit caption limit is
    defined — NOT Python's len(), which counts Unicode code points and
    undercounts any character outside the Basic Multilingual Plane (e.g.
    many emoji), which encodes as a 2-unit UTF-16 surrogate pair."""
    return len(text.encode("utf-16-le")) // 2


def _parse_response(response: requests.Response) -> dict:
    """Parse a TikTok API response, raising PublishError for every failure
    mode TikTok can return: transport-level HTTP failure, non-JSON body, or
    a 200 response wrapping a real error in its `error` field (TikTok's own
    convention — a 4xx/5xx status is not the only failure signal here)."""
    try:
        body = response.json()
    except ValueError as exc:
        raise PublishError(
            f"TikTok returned a non-JSON response (HTTP {response.status_code}): {response.text[:200]!r}",
            reason_code="MALFORMED_RESPONSE",
        ) from exc

    error = body.get("error") or {}
    error_code = error.get("code")
    if error_code not in (None, "ok"):
        raise PublishError(
            f"TikTok API error {error_code}: {error.get('message', '(no message)')}",
            reason_code=error_code or "TIKTOK_API_ERROR",
        )

    if response.status_code >= 400:
        raise PublishError(f"TikTok HTTP {response.status_code}: {body!r}", reason_code="HTTP_ERROR")

    data = body.get("data")
    if not isinstance(data, dict):
        raise PublishError(f"TikTok response missing a 'data' object: {body!r}", reason_code="MALFORMED_RESPONSE")
    return data


class TikTokPublisher(Publisher):
    """Publishes to a single dedicated TikTok test account (whichever
    account tiktok_auth.py's cached token authenticates as).

    unaudited=True (the default, and the only mode this milestone
    exercises) hard-requires SELF_ONLY — see module docstring. Pass
    unaudited=False only once this app has completed TikTok's audit for
    broader privacy levels; that path falls back to the older "requested
    level must appear in privacy_level_options" check instead.
    """

    def __init__(self, privacy_level: str = TIKTOK_DEFAULT_PRIVACY_LEVEL, unaudited: bool = True):
        self.privacy_level = privacy_level
        self.unaudited = unaudited

    def _headers(self) -> dict:
        try:
            token = get_access_token()
        except TikTokAuthError as exc:
            raise PublishError(str(exc), reason_code="AUTH_ERROR") from exc
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

    def query_creator_info(self) -> dict:
        """Query the authenticated account's publishing capabilities
        (available privacy_level_options, duration/size limits, duet/
        stitch/comment defaults). Required before every publish() call —
        never assume a hard-coded privacy level is actually offered."""
        try:
            response = requests.post(CREATOR_INFO_URL, headers=self._headers(), timeout=_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise PublishError(f"Could not reach TikTok creator_info endpoint: {exc}", reason_code="NETWORK_ERROR") from exc
        return _parse_response(response)

    def publish(self, video_path: Path, caption: str) -> PublishResult:
        video_path = Path(video_path)
        if not video_path.exists():
            raise PublishError(f"Local video not found: {video_path}", reason_code="LOCAL_FILE_MISSING")

        caption_length = _utf16_length(caption)
        if caption_length > TIKTOK_MAX_CAPTION_UTF16_UNITS:
            raise PublishError(
                f"Caption is {caption_length} UTF-16 code units, exceeding TikTok's "
                f"{TIKTOK_MAX_CAPTION_UTF16_UNITS}-unit limit. videos.caption_text is left untouched "
                "(canonical, untruncated) — this milestone fails rather than silently truncating it.",
                reason_code="CAPTION_TOO_LONG",
            )

        # Pure local check — knowable without a network call, so it runs
        # before query_creator_info() rather than after: an unaudited
        # client requesting a non-SELF_ONLY level is invalid regardless of
        # what the account's own capabilities report.
        if self.unaudited and self.privacy_level != _UNAUDITED_REQUIRED_PRIVACY_LEVEL:
            raise PublishError(
                f"Unaudited TikTok clients may only publish {_UNAUDITED_REQUIRED_PRIVACY_LEVEL} posts; "
                f"got privacy_level={self.privacy_level!r}. Pass unaudited=False only once this app "
                "has completed TikTok's audit for broader privacy levels.",
                reason_code="UNAUDITED_CLIENT_PRIVACY_RESTRICTION",
            )

        try:
            info = media.inspect_media(video_path)
        except media.MediaError as exc:
            raise PublishError(
                f"Local video failed media inspection: {exc}",
                reason_code=getattr(exc, "reason_code", "CORRUPT_MEDIA"),
            ) from exc

        creator_info = self.query_creator_info()
        privacy_options = creator_info.get("privacy_level_options") or []

        if self.unaudited:
            if _UNAUDITED_REQUIRED_PRIVACY_LEVEL not in privacy_options:
                raise PublishError(
                    f"{_UNAUDITED_REQUIRED_PRIVACY_LEVEL} is not offered for this account "
                    f"(creator_info returned: {privacy_options!r}). TikTok restricts unaudited Direct Post "
                    "clients to private accounts / SELF_ONLY — confirm the dedicated test account is private.",
                    reason_code="SELF_ONLY_UNAVAILABLE",
                )
        elif privacy_options and self.privacy_level not in privacy_options:
            raise PublishError(
                f"privacy_level={self.privacy_level!r} is not offered for this account "
                f"(available: {privacy_options}).",
                reason_code="UNSUPPORTED_PRIVACY_LEVEL",
            )

        max_duration = creator_info.get("max_video_post_duration_sec")
        if max_duration is not None and info.duration_seconds is not None and info.duration_seconds > max_duration:
            raise PublishError(
                f"Video duration {info.duration_seconds:.1f}s exceeds this account's "
                f"max_video_post_duration_sec={max_duration}.",
                reason_code="VIDEO_TOO_LONG",
            )

        video_size = video_path.stat().st_size
        init_body = {
            "post_info": {
                "title": caption,
                "privacy_level": self.privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        }
        try:
            init_response = requests.post(
                INIT_URL, headers=self._headers(), json=init_body, timeout=_REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise PublishError(f"Could not reach TikTok init endpoint: {exc}", reason_code="NETWORK_ERROR") from exc
        init_data = _parse_response(init_response)

        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")
        if not publish_id or not upload_url:
            raise PublishError(
                f"TikTok init response missing publish_id/upload_url: {init_data!r}",
                reason_code="MALFORMED_RESPONSE",
            )

        with video_path.open("rb") as f:
            video_bytes = f.read()
        try:
            upload_response = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
                },
                data=video_bytes,
                timeout=_UPLOAD_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PublishError(f"Could not upload video to TikTok: {exc}", reason_code="UPLOAD_FAILED") from exc

        if upload_response.status_code not in (200, 201, 206):
            raise PublishError(
                f"TikTok upload failed: HTTP {upload_response.status_code}: {upload_response.text[:200]!r}",
                reason_code="UPLOAD_FAILED",
            )

        return PublishResult(platform_post_id=publish_id, status="PROCESSING_UPLOAD", raw_response=init_data)

    def get_status(self, platform_post_id: str) -> PublishStatusResult:
        try:
            response = requests.post(
                STATUS_URL, headers=self._headers(), json={"publish_id": platform_post_id},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PublishError(f"Could not reach TikTok status endpoint: {exc}", reason_code="NETWORK_ERROR") from exc
        data = _parse_response(response)

        status = data.get("status")
        if not status:
            raise PublishError(f"TikTok status response missing 'status': {data!r}", reason_code="MALFORMED_RESPONSE")

        return PublishStatusResult(status=status, failure_reason=data.get("fail_reason"), raw_response=data)
