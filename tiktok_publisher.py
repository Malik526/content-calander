"""
tiktok_publisher.py — TikTokPublisher: publishes one local MP4 to TikTok via
the Content Posting API v2 (Direct Post, FILE_UPLOAD source).

What it does:
  Implements publisher.Publisher against TikTok's real, documented Content
  Posting API v2 shape: query the account's creator/privacy capabilities,
  initialize an upload, PUT the raw file bytes to the returned upload URL,
  and poll publish status. See
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

Dependencies:
  requests. tiktok_auth.py for access tokens. config.py for API base/
  default privacy level.
"""

from pathlib import Path

import requests

from config import TIKTOK_API_BASE, TIKTOK_DEFAULT_PRIVACY_LEVEL
from publisher import PublishError, PublishResult, PublishStatusResult, Publisher
from tiktok_auth import TikTokAuthError, get_access_token

CREATOR_INFO_URL = f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/"
INIT_URL = f"{TIKTOK_API_BASE}/v2/post/publish/video/init/"
STATUS_URL = f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/"

_REQUEST_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 300


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
    account tiktok_auth.py's cached token authenticates as)."""

    def __init__(self, privacy_level: str = TIKTOK_DEFAULT_PRIVACY_LEVEL):
        self.privacy_level = privacy_level

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

        creator_info = self.query_creator_info()
        privacy_options = creator_info.get("privacy_level_options") or []
        if privacy_options and self.privacy_level not in privacy_options:
            raise PublishError(
                f"privacy_level={self.privacy_level!r} is not offered for this account "
                f"(available: {privacy_options}).",
                reason_code="UNSUPPORTED_PRIVACY_LEVEL",
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
