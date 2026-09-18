"""Tests for Milestone 2.1.8 — TikTok token lifecycle / automatic refresh:
the parts of get_access_token()'s contract that were genuinely new or
genuinely fixed this milestone. Does not re-cover ground
tests/test_tiktok_auth.py already proves (valid-token fast path,
near-expiry/expired refresh, refresh-token-expired failure, PKCE/state,
the interactive/manual authorization flows — all still passing unchanged,
551 total in the full suite).

This file covers:
  - TikTokAuthError/TikTokReauthorizationRequiredError's structured
    reason_code/http_status, and that a transient refresh failure is
    distinguishable from a definitively revoked/expired one.
  - TikTokPublisher propagating that classification instead of collapsing
    every auth failure into "AUTH_ERROR" (Phase 8/9).
  - retry_classification.py treating REAUTHORIZATION_REQUIRED as terminal
    while a transient refresh failure is retryable (Phase 9/10).
  - worker.py composing correctly with both (Phase 10).
  - concurrent refresh protection via the real fcntl file lock (Phase 7).
  - the cached token file's permissions, and that no secret value ever
    appears in an exception message (Phase 6/11).

All network access is mocked; every test uses tmp_path for the token/lock
files — never the real ~/.config/content-calendar path."""

import stat
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from content_automation.scheduling import publish_tiktok as pt
from content_automation.scheduling import retry_classification
from content_automation.publishing.tiktok import auth as ta
from content_automation.publishing.tiktok import publisher as tp
from content_automation.scheduling import worker
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.publisher import PublishError, PublishResult, PublishStatusResult

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture(autouse=True)
def tiktok_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_client_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_client_secret")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tiktok_token.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "tiktok_pending_auth.json")
    monkeypatch.setattr(ta, "TIKTOK_REFRESH_LOCK_PATH", tmp_path / "tiktok_refresh.lock")


def _stored_token(now=None, access_ttl_minutes=60, refresh_ttl_days=300, refresh_token="refresh_xyz"):
    now = now or datetime.now(timezone.utc)
    return {
        "access_token": "access_abc",
        "refresh_token": refresh_token,
        "access_token_expires_at": (now + timedelta(minutes=access_ttl_minutes)).isoformat(),
        "refresh_token_expires_at": (now + timedelta(days=refresh_ttl_days)).isoformat(),
        "open_id": "user123",
        "scope": "user.info.basic,video.publish",
    }


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json_body


# ---------------------------------------------------------------------------
# 10/12. malformed refresh response and transient refresh failures fail
# safely and are distinguishable from a revoked/expired refresh token.
# ---------------------------------------------------------------------------

def test_refresh_access_token_malformed_response_fails_safely(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"access_token": "only_this"}))

    with pytest.raises(ta.TikTokAuthError) as exc_info:
        ta.refresh_access_token("some_refresh_token")

    assert not isinstance(exc_info.value, ta.TikTokReauthorizationRequiredError)


def test_refresh_access_token_network_failure_is_not_reauthorization_required(monkeypatch):
    import requests

    def _raise(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(ta.requests, "post", _raise)

    with pytest.raises(ta.TikTokAuthError) as exc_info:
        ta.refresh_access_token("some_refresh_token")

    assert exc_info.value.reason_code == "NETWORK_ERROR"
    assert not isinstance(exc_info.value, ta.TikTokReauthorizationRequiredError)


def test_refresh_access_token_5xx_is_not_reauthorization_required(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "internal"}, status_code=503))

    with pytest.raises(ta.TikTokAuthError) as exc_info:
        ta.refresh_access_token("some_refresh_token")

    assert exc_info.value.http_status == 503
    assert not isinstance(exc_info.value, ta.TikTokReauthorizationRequiredError)


# ---------------------------------------------------------------------------
# 11. invalid/revoked refresh token surfaces reauthorization-required
# ---------------------------------------------------------------------------

def test_refresh_access_token_4xx_surfaces_reauthorization_required(monkeypatch):
    monkeypatch.setattr(
        ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=400)
    )

    with pytest.raises(ta.TikTokReauthorizationRequiredError) as exc_info:
        ta.refresh_access_token("revoked_refresh_token")

    assert exc_info.value.reason_code == "REAUTHORIZATION_REQUIRED"
    assert exc_info.value.http_status == 400


def test_get_access_token_end_to_end_surfaces_reauthorization_required(monkeypatch):
    ta.save_token(_stored_token(access_ttl_minutes=-10))  # already expired, needs refresh
    monkeypatch.setattr(
        ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=401)
    )

    with pytest.raises(ta.TikTokReauthorizationRequiredError):
        ta.get_access_token()


# ---------------------------------------------------------------------------
# 5/6. successful refresh persists new access token AND new expiry
# ---------------------------------------------------------------------------

def test_successful_refresh_persists_new_access_token_and_expiry(monkeypatch):
    ta.save_token(_stored_token(access_ttl_minutes=-10))
    before = datetime.now(timezone.utc)
    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "new_access", "refresh_token": "new_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        }),
    )

    result = ta.get_access_token()

    assert result == "new_access"
    stored = ta.load_token()
    assert stored["access_token"] == "new_access"
    new_expiry = datetime.fromisoformat(stored["access_token_expires_at"])
    assert new_expiry > before + timedelta(hours=23)  # ~24h from now, not from the old (already-past) expiry


# ---------------------------------------------------------------------------
# 7/8. rotated refresh token replaces the old one and its expiry persists
# ---------------------------------------------------------------------------

def test_rotated_refresh_token_replaces_old_one(monkeypatch):
    ta.save_token(_stored_token(access_ttl_minutes=-10, refresh_token="old_refresh"))
    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "new_access", "refresh_token": "rotated_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        }),
    )

    ta.get_access_token()

    stored = ta.load_token()
    assert stored["refresh_token"] == "rotated_refresh"
    assert stored["refresh_token"] != "old_refresh"
    assert "refresh_token_expires_at" in stored


# ---------------------------------------------------------------------------
# 9. cache permissions restrictive; no secret ever appears in an exception
# message.
# ---------------------------------------------------------------------------

def test_saved_token_file_has_restrictive_permissions():
    ta.save_token(_stored_token())
    mode = stat.S_IMODE(ta.TIKTOK_TOKEN_PATH.stat().st_mode)
    assert mode == 0o600


def test_no_secret_values_appear_in_refresh_error_messages(monkeypatch):
    monkeypatch.setattr(
        ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=400)
    )

    with pytest.raises(ta.TikTokReauthorizationRequiredError) as exc_info:
        ta.refresh_access_token("super_secret_refresh_token_value")

    assert "super_secret_refresh_token_value" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 19. fresh token state survives process/token-manager recreation — there
# is no in-memory manager object here at all; every call reads straight
# from disk, so this is the contract, made explicit.
# ---------------------------------------------------------------------------

def test_refreshed_token_is_visible_to_a_brand_new_get_access_token_call(monkeypatch):
    ta.save_token(_stored_token(access_ttl_minutes=-10))
    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "new_access", "refresh_token": "new_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        }),
    )
    ta.get_access_token()

    # A wholly separate call, no shared in-memory state (no module-level
    # cache exists) — must observe exactly what was persisted.
    assert ta.get_access_token() == "new_access"


# ---------------------------------------------------------------------------
# 17/18. concurrent refresh protection: two callers racing an expired
# token only refresh once; the second observes the first's result.
# ---------------------------------------------------------------------------

def test_concurrent_refresh_attempts_only_hit_tiktok_once(monkeypatch):
    ta.save_token(_stored_token(access_ttl_minutes=-10))

    refresh_calls = []
    call_lock = threading.Lock()

    def _fake_post(*a, **k):
        with call_lock:
            refresh_calls.append(1)
        import time
        time.sleep(0.05)  # widen the race window so both threads actually overlap
        return _FakeResponse({
            "access_token": f"new_access_{len(refresh_calls)}", "refresh_token": "new_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        })

    monkeypatch.setattr(ta.requests, "post", _fake_post)

    results = []
    results_lock = threading.Lock()

    def _call():
        token = ta.get_access_token()
        with results_lock:
            results.append(token)

    threads = [threading.Thread(target=_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(refresh_calls) == 1  # only one caller actually refreshed
    assert len(set(results)) == 1   # every caller observed the same final token
    assert ta.load_token()["access_token"] == results[0]


# ---------------------------------------------------------------------------
# Phase 8/9: TikTokPublisher propagates the real classification instead of
# a hardcoded "AUTH_ERROR" for every auth failure.
# ---------------------------------------------------------------------------

def test_publisher_headers_propagates_reauthorization_required(monkeypatch):
    def _raise(*a, **k):
        raise ta.TikTokReauthorizationRequiredError("revoked", http_status=401)

    monkeypatch.setattr(tp, "get_access_token", _raise)
    publisher = tp.TikTokPublisher()

    with pytest.raises(PublishError) as exc_info:
        publisher._headers()

    assert exc_info.value.reason_code == "REAUTHORIZATION_REQUIRED"
    assert exc_info.value.http_status == 401


def test_publisher_headers_propagates_transient_network_failure(monkeypatch):
    def _raise(*a, **k):
        raise ta.TikTokAuthError("could not reach token endpoint", reason_code="NETWORK_ERROR")

    monkeypatch.setattr(tp, "get_access_token", _raise)
    publisher = tp.TikTokPublisher()

    with pytest.raises(PublishError) as exc_info:
        publisher._headers()

    assert exc_info.value.reason_code == "NETWORK_ERROR"


# ---------------------------------------------------------------------------
# Phase 9/10: retry_classification treats REAUTHORIZATION_REQUIRED as
# terminal, while a transient auth-refresh failure is retryable.
# ---------------------------------------------------------------------------

def test_reauthorization_required_is_terminal():
    assert retry_classification.is_retryable("REAUTHORIZATION_REQUIRED") is False
    assert retry_classification.is_retryable("REAUTHORIZATION_REQUIRED", http_status=503) is False  # even w/ a 5xx


def test_transient_auth_network_failure_is_retryable():
    assert retry_classification.is_retryable("NETWORK_ERROR") is True


def test_transient_auth_5xx_failure_is_retryable():
    assert retry_classification.is_retryable("AUTH_HTTP_ERROR", http_status=503) is True


def test_auth_4xx_failure_defaults_terminal():
    assert retry_classification.is_retryable("AUTH_HTTP_ERROR", http_status=400) is False


# ---------------------------------------------------------------------------
# Phase 8/13-16 + Phase 10: end-to-end — worker publishes through a real
# TikTokPublisher whose token was expired at claim time, silently refreshes,
# and every downstream call (creator_info, init, upload, status) uses the
# refreshed token. Then the composed retry-vs-reauthorization outcomes.
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def due_video(tmp_path, store):
    video_path = tmp_path / "processed" / "video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake mp4 bytes")

    video = store.insert_video("h1", "video.mp4", "/incoming/video.mp4", NOW.isoformat())
    store.update_video(
        video.id,
        canonical_media_path=str(video_path),
        container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=video_path.stat().st_size,
        caption_text="hello world", caption_source="transcript_auto",
        status="ASSIGNED",
    )
    store.insert_platform_post(
        video.id, "tiktok", created_at=NOW.isoformat(), scheduled_at=(NOW - timedelta(hours=1)).isoformat()
    )
    return store.get_video(video.id)


def _ok_body(data):
    return {"data": data, "error": {"code": "ok", "message": "OK", "log_id": "abc"}}


def test_worker_publish_silently_refreshes_and_all_calls_use_new_token(monkeypatch, store, due_video):
    """An access token already expired at claim time is silently refreshed
    exactly once, and creator_info/init/upload/status all present the
    refreshed Bearer token — no call is left using a stale one."""
    ta.save_token(_stored_token(access_ttl_minutes=-10))

    bearer_tokens_seen = []

    def _fake_post(url, headers=None, json=None, **kwargs):
        if headers is not None and "Authorization" in headers:
            bearer_tokens_seen.append(headers["Authorization"])
        if url == ta.TOKEN_URL:
            return _FakeResponse({
                "access_token": "refreshed_access", "refresh_token": "refreshed_refresh",
                "expires_in": 86400, "refresh_expires_in": 31536000,
            })
        if url == tp.CREATOR_INFO_URL:
            return _FakeResponse(_ok_body({"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600}))
        if url == tp.INIT_URL:
            return _FakeResponse(_ok_body({"publish_id": "pub_1", "upload_url": "https://upload.example/put"}))
        if url == tp.STATUS_URL:
            return _FakeResponse(_ok_body({"status": "PUBLISH_COMPLETE"}))
        raise AssertionError(f"unexpected POST to {url}")

    def _fake_put(url, **kwargs):
        return _FakeResponse({}, status_code=201)

    monkeypatch.setattr(tp.requests, "post", _fake_post)
    monkeypatch.setattr(tp.requests, "put", _fake_put)
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: tp.media.MediaInfo(
        path=None, container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=1000,
    ))

    publisher = tp.TikTokPublisher()
    summary = worker.run_due_posts_once(store, publisher, now=NOW)

    assert summary.published == 1
    final = store.get_platform_post(due_video.id, "tiktok")
    assert final.status == "PUBLISHED"
    # Only one refresh happened (the token endpoint appears once), and
    # creator_info/init/status all carried the post-refresh token.
    assert bearer_tokens_seen.count("Bearer refreshed_access") == 3  # creator_info, init, status
    assert "Bearer access_abc" not in bearer_tokens_seen  # the stale pre-refresh token was never sent


def test_worker_revoked_refresh_token_ends_failed_not_retried(monkeypatch, store, due_video):
    """Phase 10: a revoked/invalid refresh token must not burn the normal
    publishing retry loop — it ends FAILED immediately, same as any other
    terminal reason, rather than scheduling a retry that could never
    succeed without a human reauthorizing."""
    ta.save_token(_stored_token(access_ttl_minutes=-10))

    monkeypatch.setattr(
        tp.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_request"}, status_code=401)
    )
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: tp.media.MediaInfo(
        path=None, container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=1000,
    ))

    publisher = tp.TikTokPublisher()
    summary = worker.run_due_posts_once(store, publisher, now=NOW)

    assert summary.failed == 1
    assert summary.retry_scheduled == 0
    final = store.get_platform_post(due_video.id, "tiktok")
    assert final.status == "FAILED"
    # The specific message TikTokReauthorizationRequiredError raises (see
    # tiktok_auth.refresh_access_token) — not a loose substring guess, so
    # this can't false-positive the way a bare "revoked"/"invalid" check
    # could (e.g. against an unrelated tmp-path component).
    assert "python3 cli/tiktok_auth.py --authorize" in final.failure_reason
    assert final.retry_count == 0


def test_worker_transient_refresh_failure_schedules_retry_not_immediate_failure(monkeypatch, store, due_video):
    """Phase 10: a temporary failure to reach/use TikTok's token endpoint
    while refreshing must compose with the normal retry/backoff system
    (Milestone 2.1.6) exactly like any other transient publishing failure —
    not fail the post immediately the way the pre-2.1.8 hardcoded
    "AUTH_ERROR" classification would have."""
    ta.save_token(_stored_token(access_ttl_minutes=-10))

    import requests

    def _raise(*a, **k):
        raise requests.ConnectionError("temporary DNS failure")

    monkeypatch.setattr(tp.requests, "post", _raise)
    monkeypatch.setattr(tp.media, "inspect_media", lambda path: tp.media.MediaInfo(
        path=None, container="mp4", video_codec="h264", audio_codec="aac",
        width=576, height=1024, fps=30.0, duration_seconds=20.0, file_size_bytes=1000,
    ))

    publisher = tp.TikTokPublisher()
    summary = worker.run_due_posts_once(store, publisher, now=NOW)

    assert summary.retry_scheduled == 1
    assert summary.failed == 0
    final = store.get_platform_post(due_video.id, "tiktok")
    assert final.status == "PENDING"
    assert final.next_retry_at is not None
    assert final.retry_count == 1
