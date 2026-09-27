"""Unit tests for SupabaseStorage's HTTP-status-to-reason_code mapping in
put() (2026-09-27 upload-failure-semantics follow-up, triggered by a real
413 EntityTooLarge in production). Unconditional and network-free —
requests.post is monkeypatched, matching this codebase's own established
pattern of unconditional mocked-HTTP tests (e.g. test_token_verification.py,
test_api_platforms_tiktok.py) rather than depending on real Supabase
credentials the way tests/test_storage_supabase.py's real round-trip tests
do."""

from dataclasses import dataclass

import pytest

from content_automation.storage import supabase_storage
from content_automation.storage.supabase_storage import StorageError, SupabaseStorage


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""


@pytest.fixture
def storage(monkeypatch):
    # Bypass _require_configured()'s real-env-var check entirely -- this
    # test never makes a real network call, so a fake URL/key is enough.
    monkeypatch.setattr(supabase_storage, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(supabase_storage, "SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")
    return SupabaseStorage(base_url="https://fake.supabase.co", service_key="fake-service-role-key", bucket="fake-bucket")


def test_put_maps_413_to_object_too_large(storage, monkeypatch, tmp_path):
    src = tmp_path / "big.mp4"
    src.write_bytes(b"pretend this is a huge file")
    monkeypatch.setattr(
        supabase_storage.requests, "post",
        lambda *a, **k: _FakeResponse(413, '{"statusCode":"413","error":"Payload too large",'
                                            '"message":"The object exceeded the maximum allowed size",'
                                            '"code":"EntityTooLarge"}'),
    )

    with pytest.raises(StorageError) as exc_info:
        storage.put("users/1/videos/1/source.mp4", src)

    assert exc_info.value.reason_code == "OBJECT_TOO_LARGE"
    assert exc_info.value.http_status == 413


def test_put_maps_other_error_statuses_to_http_error(storage, monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"bytes")
    monkeypatch.setattr(supabase_storage.requests, "post", lambda *a, **k: _FakeResponse(500, "server error"))

    with pytest.raises(StorageError) as exc_info:
        storage.put("users/1/videos/1/source.mp4", src)

    assert exc_info.value.reason_code == "HTTP_ERROR"
    assert exc_info.value.http_status == 500


def test_put_succeeds_on_2xx(storage, monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"bytes")
    monkeypatch.setattr(supabase_storage.requests, "post", lambda *a, **k: _FakeResponse(200, "OK"))

    storage.put("users/1/videos/1/source.mp4", src)  # must not raise
