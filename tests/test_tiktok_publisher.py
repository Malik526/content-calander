"""Tests for tiktok_publisher.py: TikTokPublisher against the Content
Posting API v2 shape. All network access (requests.post/put) is mocked —
no real TikTok credentials or account required. get_access_token() is
monkeypatched directly so these tests don't depend on tiktok_auth's own
token file state. media.inspect_media is mocked by default (a real video
file isn't needed to test the TikTok-specific logic) — the duration tests
override it explicitly."""

from pathlib import Path

import pytest

import media
import tiktok_publisher as tp
from publisher import PublishError


class _FakeResponse:
    def __init__(self, json_body=None, status_code=200, text="", raise_json_error=False):
        self._json_body = json_body
        self.status_code = status_code
        self.text = text or str(json_body)
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("no json")
        return self._json_body


def _fake_media_info(duration=30.0):
    return media.MediaInfo(
        path=None, container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=duration, file_size_bytes=1000,
    )


@pytest.fixture(autouse=True)
def fake_access_token(monkeypatch):
    monkeypatch.setattr(tp, "get_access_token", lambda: "fake_access_token")


@pytest.fixture(autouse=True)
def fake_media_inspect(monkeypatch):
    """publish() now reuses media.inspect_media() to verify duration
    against the account's capabilities — default to a well-formed short
    video so tests not specifically about media inspection don't need a
    real ffmpeg-synthesized file."""
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: _fake_media_info())


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake mp4 bytes")
    return path


def _ok_body(data):
    return {"data": data, "error": {"code": "ok", "message": "OK", "log_id": "abc"}}


_SELF_ONLY_CREATOR_INFO = _ok_body({"privacy_level_options": ["SELF_ONLY"]})


# ---------------------------------------------------------------------------
# construction / headers
# ---------------------------------------------------------------------------

def test_publisher_uses_configured_default_privacy_level():
    publisher = tp.TikTokPublisher()
    assert publisher.privacy_level  # non-empty default from config


def test_publisher_accepts_explicit_privacy_level():
    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    assert publisher.privacy_level == "SELF_ONLY"


def test_publisher_defaults_to_unaudited():
    assert tp.TikTokPublisher().unaudited is True


def test_headers_include_bearer_token():
    publisher = tp.TikTokPublisher()
    headers = publisher._headers()
    assert headers["Authorization"] == "Bearer fake_access_token"


def test_headers_wrap_auth_error_as_publish_error(monkeypatch):
    from tiktok_auth import TikTokAuthError

    def _raise(*a, **k):
        raise TikTokAuthError("no credentials")

    monkeypatch.setattr(tp, "get_access_token", _raise)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher._headers()
    assert exc_info.value.reason_code == "AUTH_ERROR"


# ---------------------------------------------------------------------------
# query_creator_info
# ---------------------------------------------------------------------------

def test_query_creator_info_success(monkeypatch):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda *a, **k: _FakeResponse(_ok_body({"privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]})),
    )
    publisher = tp.TikTokPublisher()
    data = publisher.query_creator_info()
    assert data["privacy_level_options"] == ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]


def test_query_creator_info_network_error(monkeypatch):
    import requests as real_requests

    def _raise(*a, **k):
        raise real_requests.ConnectionError("boom")

    monkeypatch.setattr(tp.requests, "post", _raise)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.query_creator_info()
    assert exc_info.value.reason_code == "NETWORK_ERROR"


# ---------------------------------------------------------------------------
# publish() — missing file, successful init+upload, upload failure,
# malformed responses
# ---------------------------------------------------------------------------

def test_publish_raises_when_local_file_missing(tmp_path):
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(tmp_path / "does_not_exist.mp4", "caption")
    assert exc_info.value.reason_code == "LOCAL_FILE_MISSING"


def test_publish_success_initializes_and_uploads(monkeypatch, video_file):
    posts = []

    def fake_post(url, **kwargs):
        posts.append(url)
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_SELF_ONLY_CREATOR_INFO)
        if url == tp.INIT_URL:
            assert kwargs["json"]["post_info"]["title"] == "my caption"
            assert kwargs["json"]["post_info"]["privacy_level"] == "SELF_ONLY"
            return _FakeResponse(_ok_body({"publish_id": "pub_123", "upload_url": "https://upload.example.com/x"}))
        raise AssertionError(f"unexpected POST to {url}")

    puts = []

    def fake_put(url, **kwargs):
        puts.append((url, kwargs))
        return _FakeResponse(status_code=201)

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.requests, "put", fake_put)

    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    result = publisher.publish(video_file, "my caption")

    assert result.platform_post_id == "pub_123"
    assert result.status == "PROCESSING_UPLOAD"
    assert puts[0][0] == "https://upload.example.com/x"
    assert puts[0][1]["data"] == b"fake mp4 bytes"
    assert "Content-Range" in puts[0][1]["headers"]


def test_publish_init_missing_publish_id_is_malformed_response(monkeypatch, video_file):
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_SELF_ONLY_CREATOR_INFO)
        return _FakeResponse(_ok_body({"upload_url": "https://upload.example.com/x"}))  # no publish_id

    monkeypatch.setattr(tp.requests, "post", fake_post)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "MALFORMED_RESPONSE"


def test_publish_upload_http_failure(monkeypatch, video_file):
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_SELF_ONLY_CREATOR_INFO)
        return _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=500, text="server error"))

    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "UPLOAD_FAILED"


def test_publish_upload_network_error(monkeypatch, video_file):
    import requests as real_requests

    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_SELF_ONLY_CREATOR_INFO)
        return _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))

    def fake_put(*a, **k):
        raise real_requests.Timeout("timed out")

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.requests, "put", fake_put)

    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "UPLOAD_FAILED"


def test_publish_tiktok_api_error_response(monkeypatch, video_file):
    """A 200 response can still wrap a real TikTok error in `error` —
    must raise using that error's own code, not silently succeed."""
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse({"data": {}, "error": {"code": "access_token_invalid", "message": "bad token"}})
        raise AssertionError("should not reach init after creator_info errors")

    monkeypatch.setattr(tp.requests, "post", fake_post)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "access_token_invalid"


def test_publish_non_json_response_is_malformed(monkeypatch, video_file):
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _FakeResponse(raise_json_error=True, text="<html>"))
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "MALFORMED_RESPONSE"


# ---------------------------------------------------------------------------
# Unaudited-client SELF_ONLY restriction
# ---------------------------------------------------------------------------

def test_publish_unaudited_requires_self_only_present(monkeypatch, video_file):
    """Default (unaudited=True): SELF_ONLY must actually appear in
    creator_info's privacy_level_options — an empty/absent list must NOT
    be treated as "no restriction", unlike the older generic check."""
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"privacy_level_options": ["PUBLIC_TO_EVERYONE"]})),
    )
    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "SELF_ONLY_UNAVAILABLE"


def test_publish_unaudited_fails_when_privacy_level_options_empty(monkeypatch, video_file):
    monkeypatch.setattr(tp.requests, "post", lambda url, **k: _FakeResponse(_ok_body({})))
    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "SELF_ONLY_UNAVAILABLE"


def test_publish_unaudited_rejects_non_self_only_requested_level(video_file):
    """unaudited=True must refuse to even attempt a non-SELF_ONLY post,
    without needing a network call to know that."""
    publisher = tp.TikTokPublisher(privacy_level="PUBLIC_TO_EVERYONE", unaudited=True)
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "UNAUDITED_CLIENT_PRIVACY_RESTRICTION"


def test_publish_unaudited_succeeds_when_self_only_offered(monkeypatch, video_file):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: (
            _FakeResponse(_SELF_ONLY_CREATOR_INFO) if url == tp.CREATOR_INFO_URL
            else _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        ),
    )
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    result = tp.TikTokPublisher().publish(video_file, "caption")
    assert result.platform_post_id == "pub_1"


def test_publish_audited_mode_falls_back_to_generic_privacy_check(monkeypatch, video_file):
    """unaudited=False preserves the older, more permissive check (skip
    validation entirely when privacy_level_options is empty/absent) —
    this is the path a future audited client would use; it must keep
    working even though the default (unaudited=True) path is now stricter."""
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: (
            _FakeResponse(_ok_body({})) if url == tp.CREATOR_INFO_URL
            else _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        ),
    )
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    publisher = tp.TikTokPublisher(privacy_level="PUBLIC_TO_EVERYONE", unaudited=False)
    result = publisher.publish(video_file, "caption")
    assert result.platform_post_id == "pub_1"


def test_publish_audited_mode_rejects_unoffered_level(monkeypatch, video_file):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"privacy_level_options": ["PUBLIC_TO_EVERYONE"]})),
    )
    publisher = tp.TikTokPublisher(privacy_level="MUTUAL_FOLLOW_FRIENDS", unaudited=False)
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "UNSUPPORTED_PRIVACY_LEVEL"


# ---------------------------------------------------------------------------
# TikTok-bound caption length (UTF-16 code units, not Python len())
# ---------------------------------------------------------------------------

def test_utf16_length_counts_bmp_characters_like_python_len():
    assert tp._utf16_length("hello") == 5


def test_utf16_length_counts_non_bmp_characters_as_surrogate_pairs():
    """U+1F600 (😀) is 1 Python character but a 2-unit UTF-16 surrogate
    pair — this is exactly the gap naive len() misses."""
    emoji = "\U0001F600"
    assert len(emoji) == 1  # Python code-point count
    assert tp._utf16_length(emoji) == 2  # real UTF-16 code-unit count


def test_publish_accepts_caption_at_exactly_the_limit(monkeypatch, video_file):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: (
            _FakeResponse(_SELF_ONLY_CREATOR_INFO) if url == tp.CREATOR_INFO_URL
            else _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        ),
    )
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    caption = "a" * tp.TIKTOK_MAX_CAPTION_UTF16_UNITS
    result = tp.TikTokPublisher().publish(video_file, caption)
    assert result.platform_post_id == "pub_1"


def test_publish_rejects_caption_one_over_the_limit(video_file):
    caption = "a" * (tp.TIKTOK_MAX_CAPTION_UTF16_UNITS + 1)
    with pytest.raises(PublishError) as exc_info:
        tp.TikTokPublisher().publish(video_file, caption)
    assert exc_info.value.reason_code == "CAPTION_TOO_LONG"


def test_publish_rejects_caption_over_limit_measured_in_utf16_units_not_python_chars(video_file):
    """A caption that is under the limit in Python character count but
    over it in UTF-16 code units (because of non-BMP characters) must
    still be rejected — this is the whole point of measuring UTF-16 units."""
    limit = tp.TIKTOK_MAX_CAPTION_UTF16_UNITS
    # Each emoji is 1 Python char / 2 UTF-16 units. (limit // 2) + 1 emoji
    # is well under `limit` Python characters but over `limit` UTF-16 units.
    emoji_count = (limit // 2) + 1
    caption = "\U0001F600" * emoji_count
    assert len(caption) < limit  # under the limit by naive Python len()
    assert tp._utf16_length(caption) > limit  # over the limit in real UTF-16 units

    with pytest.raises(PublishError) as exc_info:
        tp.TikTokPublisher().publish(video_file, caption)
    assert exc_info.value.reason_code == "CAPTION_TOO_LONG"


def test_caption_length_checked_before_any_network_call(monkeypatch, video_file):
    def fail_if_called(*a, **k):
        raise AssertionError("must not reach the network for an over-limit caption")

    monkeypatch.setattr(tp.requests, "post", fail_if_called)
    caption = "a" * (tp.TIKTOK_MAX_CAPTION_UTF16_UNITS + 1)
    with pytest.raises(PublishError) as exc_info:
        tp.TikTokPublisher().publish(video_file, caption)
    assert exc_info.value.reason_code == "CAPTION_TOO_LONG"


def test_caption_text_is_never_mutated_by_validation(video_file):
    """videos.caption_text stays canonical/untouched — publish() only
    raises, it never derives or returns a modified caption."""
    caption = "a" * (tp.TIKTOK_MAX_CAPTION_UTF16_UNITS + 1)
    original = caption
    with pytest.raises(PublishError):
        tp.TikTokPublisher().publish(video_file, caption)
    assert caption == original


# ---------------------------------------------------------------------------
# Creator-specific duration capability
# ---------------------------------------------------------------------------

def test_publish_rejects_video_exceeding_account_max_duration(monkeypatch, video_file):
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: _fake_media_info(duration=120.0))
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60})),
    )
    with pytest.raises(PublishError) as exc_info:
        tp.TikTokPublisher().publish(video_file, "caption")
    assert exc_info.value.reason_code == "VIDEO_TOO_LONG"


def test_publish_accepts_video_within_account_max_duration(monkeypatch, video_file):
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: _fake_media_info(duration=30.0))
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: (
            _FakeResponse(_ok_body({"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60}))
            if url == tp.CREATOR_INFO_URL
            else _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        ),
    )
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    result = tp.TikTokPublisher().publish(video_file, "caption")
    assert result.platform_post_id == "pub_1"


def test_publish_skips_duration_check_when_capability_not_reported(monkeypatch, video_file):
    """No max_video_post_duration_sec in creator_info at all must not be
    treated as "reject everything" — only a reported, exceeded limit
    blocks publishing."""
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: _fake_media_info(duration=99999.0))
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: (
            _FakeResponse(_SELF_ONLY_CREATOR_INFO) if url == tp.CREATOR_INFO_URL
            else _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        ),
    )
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    result = tp.TikTokPublisher().publish(video_file, "caption")
    assert result.platform_post_id == "pub_1"


def test_publish_raises_when_media_inspection_fails(monkeypatch, video_file):
    def raise_media_error(path):
        raise media.CorruptMediaError("corrupt")

    monkeypatch.setattr(tp.media, "inspect_media", raise_media_error)
    with pytest.raises(PublishError) as exc_info:
        tp.TikTokPublisher().publish(video_file, "caption")
    assert exc_info.value.reason_code == "CORRUPT_MEDIA"


# ---------------------------------------------------------------------------
# get_status — polling
# ---------------------------------------------------------------------------

def test_get_status_publish_complete(monkeypatch):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["123"]})),
    )
    publisher = tp.TikTokPublisher()
    result = publisher.get_status("pub_123")
    assert result.status == "PUBLISH_COMPLETE"
    assert result.failure_reason is None


def test_get_status_failed(monkeypatch):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"status": "FAILED", "fail_reason": "video_pull_failed"})),
    )
    publisher = tp.TikTokPublisher()
    result = publisher.get_status("pub_123")
    assert result.status == "FAILED"
    assert result.failure_reason == "video_pull_failed"


def test_get_status_sends_correct_publish_id(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(_ok_body({"status": "PROCESSING_DOWNLOAD"}))

    monkeypatch.setattr(tp.requests, "post", fake_post)
    tp.TikTokPublisher().get_status("pub_456")

    assert captured["url"] == tp.STATUS_URL
    assert captured["json"] == {"publish_id": "pub_456"}


def test_get_status_missing_status_field_is_malformed(monkeypatch):
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _FakeResponse(_ok_body({})))
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.get_status("pub_123")
    assert exc_info.value.reason_code == "MALFORMED_RESPONSE"


def test_get_status_network_error(monkeypatch):
    import requests as real_requests

    def _raise(*a, **k):
        raise real_requests.ConnectionError("boom")

    monkeypatch.setattr(tp.requests, "post", _raise)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.get_status("pub_123")
    assert exc_info.value.reason_code == "NETWORK_ERROR"


def test_get_status_http_error_status(monkeypatch):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda *a, **k: _FakeResponse({"data": {}, "error": {"code": "ok"}}, status_code=500),
    )
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.get_status("pub_123")
    assert exc_info.value.reason_code == "HTTP_ERROR"


def test_response_missing_data_object_is_malformed(monkeypatch):
    """A response with no 'error' and no usable 'data' at all should still
    be treated as malformed rather than silently returning {}."""
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _FakeResponse({"error": {"code": "ok"}}))
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.get_status("pub_123")
    assert exc_info.value.reason_code == "MALFORMED_RESPONSE"
