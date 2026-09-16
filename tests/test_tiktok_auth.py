"""Tests for tiktok_auth.py: token persistence, authorization URL
construction, exchange/refresh, and the get_access_token() expiry/refresh
contract. All network access (requests.post) is mocked — no real TikTok
credentials or account required."""

from datetime import datetime, timedelta, timezone

import pytest

import tiktok_auth as ta


@pytest.fixture(autouse=True)
def tiktok_credentials(monkeypatch, tmp_path):
    """Every test gets a fake client key/secret/redirect + an isolated
    token file path, so tests never touch the real ~/.config path or
    depend on .env contents."""
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_client_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_client_secret")
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tiktok_token.json")


def _valid_token(now=None, access_ttl_minutes=60, refresh_ttl_days=300):
    now = now or datetime.now(timezone.utc)
    return {
        "access_token": "access_abc",
        "refresh_token": "refresh_xyz",
        "access_token_expires_at": (now + timedelta(minutes=access_ttl_minutes)).isoformat(),
        "refresh_token_expires_at": (now + timedelta(days=refresh_ttl_days)).isoformat(),
        "open_id": "user123",
        "scope": "user.info.basic,video.publish",
    }


# ---------------------------------------------------------------------------
# token persistence
# ---------------------------------------------------------------------------

def test_load_token_returns_none_when_absent():
    assert ta.load_token() is None


def test_save_then_load_token_roundtrips():
    token = _valid_token()
    ta.save_token(token)
    assert ta.load_token() == token


def test_load_token_returns_none_for_corrupt_file():
    ta.TIKTOK_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    ta.TIKTOK_TOKEN_PATH.write_text("not json", encoding="utf-8")
    assert ta.load_token() is None


# ---------------------------------------------------------------------------
# authorization URL
# ---------------------------------------------------------------------------

def test_build_authorization_url_contains_required_params():
    url = ta.build_authorization_url(state="mystate")
    assert url.startswith(ta.AUTHORIZE_URL)
    assert "client_key=fake_client_key" in url
    assert "state=mystate" in url
    assert "redirect_uri=" in url
    assert "response_type=code" in url


def test_build_authorization_url_requires_client_credentials(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "")
    with pytest.raises(ta.TikTokAuthError, match="TIKTOK_CLIENT_KEY"):
        ta.build_authorization_url(state="x")


def test_build_authorization_url_requires_redirect_uri(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "")
    with pytest.raises(ta.TikTokAuthError, match="TIKTOK_REDIRECT_URI"):
        ta.build_authorization_url(state="x")


# ---------------------------------------------------------------------------
# exchange / refresh (requests.post mocked)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json_body


def test_exchange_code_for_token_success(monkeypatch):
    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "access_abc", "refresh_token": "refresh_xyz",
            "expires_in": 86400, "refresh_expires_in": 31536000,
            "open_id": "user123", "scope": "user.info.basic,video.publish",
        }),
    )
    token = ta.exchange_code_for_token("authcode123")
    assert token["access_token"] == "access_abc"
    assert token["refresh_token"] == "refresh_xyz"
    assert "access_token_expires_at" in token
    assert "refresh_token_expires_at" in token


def test_exchange_code_for_token_requires_client_credentials(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "")
    with pytest.raises(ta.TikTokAuthError):
        ta.exchange_code_for_token("authcode123")


def test_exchange_code_for_token_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_grant"}, status_code=400))
    with pytest.raises(ta.TikTokAuthError, match="400"):
        ta.exchange_code_for_token("bad_code")


def test_exchange_code_for_token_raises_on_missing_field(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"access_token": "only_this"}))
    with pytest.raises(ta.TikTokAuthError, match="access_token|refresh_token|expires_in"):
        ta.exchange_code_for_token("authcode123")


def test_exchange_code_for_token_raises_on_non_json_response(monkeypatch):
    class _BadResponse:
        status_code = 200
        text = "<html>not json</html>"

        def json(self):
            raise ValueError("no json")

    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _BadResponse())
    with pytest.raises(ta.TikTokAuthError, match="non-JSON"):
        ta.exchange_code_for_token("authcode123")


def test_refresh_access_token_success(monkeypatch):
    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "new_access", "refresh_token": "new_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        }),
    )
    token = ta.refresh_access_token("old_refresh_token")
    assert token["access_token"] == "new_access"


# ---------------------------------------------------------------------------
# get_access_token — the expiry/refresh contract
# ---------------------------------------------------------------------------

def test_get_access_token_raises_actionable_error_when_never_authorized():
    with pytest.raises(ta.TikTokAuthError, match="tiktok_auth.py --print-auth-url"):
        ta.get_access_token()


def test_get_access_token_returns_cached_token_when_still_valid():
    ta.save_token(_valid_token(access_ttl_minutes=60))
    assert ta.get_access_token() == "access_abc"


def test_get_access_token_refreshes_when_near_expiry(monkeypatch):
    ta.save_token(_valid_token(access_ttl_minutes=1))  # inside the 5-minute refresh skew

    monkeypatch.setattr(
        ta.requests, "post",
        lambda *a, **k: _FakeResponse({
            "access_token": "refreshed_access", "refresh_token": "refreshed_refresh",
            "expires_in": 86400, "refresh_expires_in": 31536000,
        }),
    )

    result = ta.get_access_token()

    assert result == "refreshed_access"
    assert ta.load_token()["access_token"] == "refreshed_access"  # refreshed token persisted


def test_get_access_token_raises_when_refresh_token_also_expired():
    ta.save_token(_valid_token(access_ttl_minutes=-10, refresh_ttl_days=-1))
    with pytest.raises(ta.TikTokAuthError, match="expired"):
        ta.get_access_token()
