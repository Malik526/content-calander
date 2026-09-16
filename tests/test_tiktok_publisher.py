"""Tests for tiktok_publisher.py: TikTokPublisher against the Content
Posting API v2 shape. All network access (requests.post/put) is mocked —
no real TikTok credentials or account required. get_access_token() is
monkeypatched directly so these tests don't depend on tiktok_auth's own
token file state."""

from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def fake_access_token(monkeypatch):
    monkeypatch.setattr(tp, "get_access_token", lambda: "fake_access_token")


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake mp4 bytes")
    return path


def _ok_body(data):
    return {"data": data, "error": {"code": "ok", "message": "OK", "log_id": "abc"}}


# ---------------------------------------------------------------------------
# construction / headers
# ---------------------------------------------------------------------------

def test_publisher_uses_configured_default_privacy_level():
    publisher = tp.TikTokPublisher()
    assert publisher.privacy_level  # non-empty default from config


def test_publisher_accepts_explicit_privacy_level():
    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    assert publisher.privacy_level == "SELF_ONLY"


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
# unsupported privacy level, malformed responses
# ---------------------------------------------------------------------------

def test_publish_raises_when_local_file_missing(tmp_path):
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(tmp_path / "does_not_exist.mp4", "caption")
    assert exc_info.value.reason_code == "LOCAL_FILE_MISSING"


def test_publish_rejects_unsupported_privacy_level(monkeypatch, video_file):
    monkeypatch.setattr(
        tp.requests, "post",
        lambda url, **k: _FakeResponse(_ok_body({"privacy_level_options": ["PUBLIC_TO_EVERYONE"]})),
    )
    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "UNSUPPORTED_PRIVACY_LEVEL"


def test_publish_success_initializes_and_uploads(monkeypatch, video_file):
    posts = []

    def fake_post(url, **kwargs):
        posts.append(url)
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_ok_body({"privacy_level_options": ["SELF_ONLY"]}))
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


def test_publish_skips_privacy_check_when_no_options_returned(monkeypatch, video_file):
    """Some accounts/sandboxes may not return privacy_level_options at all
    — must not fail closed on an empty/absent list, only on a populated
    one that doesn't include the requested level."""
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_ok_body({}))
        if url == tp.INIT_URL:
            return _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example.com/x"}))
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.requests, "put", lambda *a, **k: _FakeResponse(status_code=200))

    publisher = tp.TikTokPublisher(privacy_level="SELF_ONLY")
    result = publisher.publish(video_file, "caption")
    assert result.platform_post_id == "pub_1"


def test_publish_init_missing_publish_id_is_malformed_response(monkeypatch, video_file):
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_ok_body({}))
        return _FakeResponse(_ok_body({"upload_url": "https://upload.example.com/x"}))  # no publish_id

    monkeypatch.setattr(tp.requests, "post", fake_post)
    publisher = tp.TikTokPublisher()
    with pytest.raises(PublishError) as exc_info:
        publisher.publish(video_file, "caption")
    assert exc_info.value.reason_code == "MALFORMED_RESPONSE"


def test_publish_upload_http_failure(monkeypatch, video_file):
    def fake_post(url, **kwargs):
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_ok_body({}))
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
            return _FakeResponse(_ok_body({}))
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
            return _FakeResponse(_ok_body({}))
        return _FakeResponse({"data": {}, "error": {"code": "access_token_invalid", "message": "bad token"}})

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
