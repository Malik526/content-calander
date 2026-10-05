"""Unit tests for short-lived signed media URLs (Milestone 4.0): lifetime
bounds, log redaction, SupabaseStorage.create_signed_url's HTTP contract
(requests mocked) and LocalStorage's refusal. The real-bucket round trip is
in test_storage_supabase.py."""

import pytest

from content_automation.storage import supabase_storage
from content_automation.storage.local import LocalStorage, StorageObjectNotFoundError
from content_automation.storage.signed_urls import (
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    SignedUrlUnsupportedError,
    redact_signed_url,
    validate_ttl,
)
from content_automation.storage.supabase_storage import StorageError, SupabaseStorage


class _Response:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setattr(supabase_storage, "_require_configured", lambda: None)
    return SupabaseStorage(base_url="https://proj.supabase.co/", service_key="service-key", bucket="media")


def _mock_post(monkeypatch, response, calls):
    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return response
    monkeypatch.setattr(supabase_storage.requests, "post", fake_post)


@pytest.mark.parametrize("ttl", [MIN_TTL_SECONDS, 3600, MAX_TTL_SECONDS])
def test_validate_ttl_accepts_the_allowed_range(ttl):
    assert validate_ttl(ttl) == ttl


@pytest.mark.parametrize("ttl", [0, MIN_TTL_SECONDS - 1, MAX_TTL_SECONDS + 1, 7 * 86_400])
def test_validate_ttl_rejects_too_short_or_long_lived_links(ttl):
    with pytest.raises(ValueError):
        validate_ttl(ttl)


def test_redaction_drops_the_token_but_keeps_the_object_path():
    url = "https://proj.supabase.co/storage/v1/object/sign/media/users/2/videos/15/source.mov?token=eyJsecret.part"
    redacted = redact_signed_url(url)
    assert "eyJsecret" not in redacted and "token" not in redacted
    assert redacted == "https://proj.supabase.co/storage/v1/object/sign/media/users/2/videos/15/source.mov?[redacted]"
    assert redact_signed_url("https://example.com/plain.mp4") == "https://example.com/plain.mp4"


def test_create_signed_url_calls_the_sign_endpoint_and_returns_an_absolute_url(storage, monkeypatch):
    calls = []
    _mock_post(monkeypatch, _Response(200, {"signedURL": "/object/sign/media/users/2/v.mp4?token=abc"}), calls)

    url = storage.create_signed_url("users/2/v.mp4", 3600)

    assert url == "https://proj.supabase.co/storage/v1/object/sign/media/users/2/v.mp4?token=abc"
    assert calls[0]["url"] == "https://proj.supabase.co/storage/v1/object/sign/media/users/2/v.mp4"
    assert calls[0]["json"] == {"expiresIn": 3600}
    assert calls[0]["headers"]["Authorization"] == "Bearer service-key"


def test_create_signed_url_validates_lifetime_before_any_request(storage, monkeypatch):
    calls = []
    _mock_post(monkeypatch, _Response(200, {"signedURL": "/x"}), calls)
    with pytest.raises(ValueError):
        storage.create_signed_url("k.mp4", 30 * 86_400)
    assert calls == []


@pytest.mark.parametrize("response", [_Response(404, text="not found"), _Response(400, text='{"error":"not_found","message":"Object not found"}')])
def test_create_signed_url_for_a_missing_object_raises_not_found(storage, monkeypatch, response):
    _mock_post(monkeypatch, response, [])
    with pytest.raises(StorageObjectNotFoundError):
        storage.create_signed_url("missing.mp4", 600)


def test_create_signed_url_http_and_malformed_failures_raise_storage_error_without_a_url(storage, monkeypatch):
    _mock_post(monkeypatch, _Response(500, text="boom"), [])
    with pytest.raises(StorageError) as http_error:
        storage.create_signed_url("k.mp4", 600)
    assert http_error.value.reason_code == "HTTP_ERROR"

    _mock_post(monkeypatch, _Response(200, {"unexpected": True}), [])
    with pytest.raises(StorageError) as malformed:
        storage.create_signed_url("k.mp4", 600)
    assert malformed.value.reason_code == "MALFORMED_RESPONSE"


def test_local_storage_refuses_to_issue_signed_urls(tmp_path):
    with pytest.raises(SignedUrlUnsupportedError, match="STORAGE_BACKEND=supabase"):
        LocalStorage(root=tmp_path).create_signed_url("anything.mp4", 600)
