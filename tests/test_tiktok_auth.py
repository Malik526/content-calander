"""Tests for tiktok_auth.py: token persistence, PKCE/state generation, the
interactive localhost-callback flow, the manual two-command fallback,
exchange/refresh, and the get_access_token() expiry/refresh contract. All
network access (requests.post) is mocked — no real TikTok credentials or
account required. The interactive flow tests use a real local HTTP
round-trip (127.0.0.1, no external network) rather than mocking the
callback itself, since that's the part most worth actually exercising."""

import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

import tiktok_auth as ta


@pytest.fixture(autouse=True)
def tiktok_credentials(monkeypatch, tmp_path):
    """Every test gets a fake client key/secret/redirect + isolated token/
    pending-auth file paths, so tests never touch the real ~/.config path
    or depend on .env contents."""
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "fake_client_key")
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_SECRET", "fake_client_secret")
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "")
    monkeypatch.setattr(ta, "TIKTOK_TOKEN_PATH", tmp_path / "tiktok_token.json")
    monkeypatch.setattr(ta, "TIKTOK_PENDING_AUTH_PATH", tmp_path / "tiktok_pending_auth.json")


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json_body


def _fake_token_post(*a, **k):
    return _FakeResponse({
        "access_token": "access_abc", "refresh_token": "refresh_xyz",
        "expires_in": 86400, "refresh_expires_in": 31536000,
        "open_id": "user123", "scope": "user.info.basic,video.publish",
    })


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
# PKCE / state generation
# ---------------------------------------------------------------------------

def test_generate_pkce_pair_verifier_length_and_charset():
    verifier, _ = ta.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    # TikTok's unreserved charset: [A-Z] [a-z] [0-9] - . _ ~
    assert all(c.isalnum() or c in "-._~" for c in verifier)


def test_generate_pkce_pair_challenge_is_64_hex_chars():
    _, challenge = ta.generate_pkce_pair()
    assert len(challenge) == 64  # hex(sha256(...)) is always 64 lowercase hex chars
    assert all(c in "0123456789abcdef" for c in challenge)


def test_generate_pkce_pair_challenge_matches_verifier_via_s256():
    """TikTok's Desktop Login Kit requires the hex digest of SHA256(verifier),
    not RFC 7636's standard base64url encoding — this is the exact bug this
    test guards against regressing to."""
    import hashlib

    verifier, challenge = ta.generate_pkce_pair()
    expected = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    assert challenge == expected


def test_generate_pkce_pair_is_fresh_every_call():
    v1, c1 = ta.generate_pkce_pair()
    v2, c2 = ta.generate_pkce_pair()
    assert v1 != v2
    assert c1 != c2


def test_generate_state_is_fresh_every_call():
    assert ta.generate_state() != ta.generate_state()


# ---------------------------------------------------------------------------
# authorization URL
# ---------------------------------------------------------------------------

def test_build_authorization_url_contains_required_pkce_params():
    url = ta.build_authorization_url(state="mystate", code_challenge="mychallenge", redirect_uri="http://127.0.0.1:1234/callback")
    assert url.startswith(ta.AUTHORIZE_URL)
    assert "client_key=fake_client_key" in url
    assert "state=mystate" in url
    assert "code_challenge=mychallenge" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url


def test_build_authorization_url_requires_client_credentials(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "")
    with pytest.raises(ta.TikTokAuthError, match="TIKTOK_CLIENT_KEY"):
        ta.build_authorization_url(state="x", code_challenge="y", redirect_uri="http://127.0.0.1:1/x")


# ---------------------------------------------------------------------------
# exchange / refresh (requests.post mocked)
# ---------------------------------------------------------------------------

def test_exchange_code_for_token_success(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", _fake_token_post)
    token = ta.exchange_code_for_token("authcode123", code_verifier="v", redirect_uri="http://127.0.0.1:1/x")
    assert token["access_token"] == "access_abc"
    assert token["refresh_token"] == "refresh_xyz"
    assert "access_token_expires_at" in token
    assert "refresh_token_expires_at" in token


def test_exchange_code_for_token_sends_code_verifier(monkeypatch):
    captured = {}

    def fake_post(url, data, **kwargs):
        captured["data"] = data
        return _fake_token_post()

    monkeypatch.setattr(ta.requests, "post", fake_post)
    ta.exchange_code_for_token("authcode123", code_verifier="my_verifier_value", redirect_uri="http://127.0.0.1:1/x")

    assert captured["data"]["code_verifier"] == "my_verifier_value"
    assert captured["data"]["grant_type"] == "authorization_code"


def test_exchange_code_for_token_requires_client_credentials(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_CLIENT_KEY", "")
    with pytest.raises(ta.TikTokAuthError):
        ta.exchange_code_for_token("authcode123", code_verifier="v", redirect_uri="http://127.0.0.1:1/x")


def test_exchange_code_for_token_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"error": "invalid_grant"}, status_code=400))
    with pytest.raises(ta.TikTokAuthError, match="400"):
        ta.exchange_code_for_token("bad_code", code_verifier="v", redirect_uri="http://127.0.0.1:1/x")


def test_exchange_code_for_token_raises_on_missing_field(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _FakeResponse({"access_token": "only_this"}))
    with pytest.raises(ta.TikTokAuthError, match="access_token|refresh_token|expires_in"):
        ta.exchange_code_for_token("authcode123", code_verifier="v", redirect_uri="http://127.0.0.1:1/x")


def test_exchange_code_for_token_raises_on_non_json_response(monkeypatch):
    class _BadResponse:
        status_code = 200
        text = "<html>not json</html>"

        def json(self):
            raise ValueError("no json")

    monkeypatch.setattr(ta.requests, "post", lambda *a, **k: _BadResponse())
    with pytest.raises(ta.TikTokAuthError, match="non-JSON"):
        ta.exchange_code_for_token("authcode123", code_verifier="v", redirect_uri="http://127.0.0.1:1/x")


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


def test_refresh_access_token_does_not_send_code_verifier(monkeypatch):
    captured = {}

    def fake_post(url, data, **kwargs):
        captured["data"] = data
        return _FakeResponse({"access_token": "a", "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 1})

    monkeypatch.setattr(ta.requests, "post", fake_post)
    ta.refresh_access_token("old_refresh_token")

    assert "code_verifier" not in captured["data"]
    assert captured["data"]["grant_type"] == "refresh_token"


# ---------------------------------------------------------------------------
# get_access_token — the expiry/refresh contract
# ---------------------------------------------------------------------------

def test_get_access_token_raises_actionable_error_when_never_authorized():
    with pytest.raises(ta.TikTokAuthError, match="tiktok_auth.py --authorize"):
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


# ---------------------------------------------------------------------------
# interactive flow — real localhost callback, no external network
# ---------------------------------------------------------------------------

def _click_callback(port: int, path: str, params: str, delay: float = 0.15) -> None:
    time.sleep(delay)
    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}?{params}", timeout=5)


def test_authorize_interactive_full_round_trip_succeeds(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", _fake_token_post)

    # authorize_interactive generates `state` internally, so the thread
    # simulating the browser callback polls a value the spy below fills in
    # as soon as it's known, rather than needing it up front.
    captured_state = {}
    orig_build = ta.build_authorization_url

    def spy_build(state, code_challenge, redirect_uri):
        captured_state["state"] = state
        return orig_build(state, code_challenge, redirect_uri)

    monkeypatch.setattr(ta, "build_authorization_url", spy_build)

    port = _free_port()

    def click_once_state_known():
        for _ in range(50):
            if "state" in captured_state:
                break
            time.sleep(0.01)
        _click_callback(port, "/callback", f"code=fakecode123&state={captured_state['state']}", delay=0)

    threading.Thread(target=click_once_state_known, daemon=True).start()

    token = ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=5)

    assert token["access_token"] == "access_abc"
    assert ta.load_token()["access_token"] == "access_abc"


def test_authorize_interactive_rejects_state_mismatch(monkeypatch):
    monkeypatch.setattr(ta.requests, "post", _fake_token_post)
    port = _free_port()
    threading.Thread(
        target=_click_callback, args=(port, "/callback", "code=fakecode123&state=totally_wrong_state"), daemon=True,
    ).start()

    with pytest.raises(ta.TikTokAuthError, match="state mismatch"):
        ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=5)

    assert ta.load_token() is None  # never exchanged, never persisted


def test_authorize_interactive_surfaces_oauth_error_callback():
    port = _free_port()
    threading.Thread(
        target=_click_callback, args=(port, "/callback", "error=access_denied&error_description=user+declined"), daemon=True,
    ).start()

    with pytest.raises(ta.TikTokAuthError, match="access_denied"):
        ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=5)


def test_authorize_interactive_times_out_with_no_callback():
    port = _free_port()
    with pytest.raises(ta.TikTokAuthError, match="timed out|No authorization"):
        ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=1)


def test_authorize_interactive_uses_fresh_verifier_each_call(monkeypatch):
    """Two separate interactive attempts must never share a PKCE verifier —
    verified by capturing what's actually sent to the token endpoint."""
    verifiers_sent = []

    def fake_post(url, data, **kwargs):
        verifiers_sent.append(data.get("code_verifier"))
        return _fake_token_post()

    monkeypatch.setattr(ta.requests, "post", fake_post)

    for _ in range(2):
        captured_state = {}
        orig_build = ta.build_authorization_url

        def spy_build(state, code_challenge, redirect_uri, _orig=orig_build):
            captured_state["state"] = state
            return _orig(state, code_challenge, redirect_uri)

        monkeypatch.setattr(ta, "build_authorization_url", spy_build)
        port = _free_port()

        def click_once_state_known(_port=port, _captured=captured_state):
            for _ in range(50):
                if "state" in _captured:
                    break
                time.sleep(0.01)
            _click_callback(_port, "/callback", f"code=fakecode123&state={_captured['state']}", delay=0)

        threading.Thread(target=click_once_state_known, daemon=True).start()
        ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=5)

    assert len(verifiers_sent) == 2
    assert verifiers_sent[0] != verifiers_sent[1]


def test_authorize_interactive_sends_same_verifier_that_produced_the_challenge(monkeypatch):
    """End-to-end guard for the PKCE mismatch bug: the exact code_verifier
    used to derive code_challenge for the authorization URL must be the
    same one presented at token exchange — never regenerated, truncated,
    or otherwise altered in between."""
    sent_verifier = {}

    def fake_post(url, data, **kwargs):
        sent_verifier["value"] = data.get("code_verifier")
        return _fake_token_post()

    monkeypatch.setattr(ta.requests, "post", fake_post)

    captured = {}
    orig_generate = ta.generate_pkce_pair

    def spy_generate():
        verifier, challenge = orig_generate()
        captured["verifier"] = verifier
        captured["challenge"] = challenge
        return verifier, challenge

    monkeypatch.setattr(ta, "generate_pkce_pair", spy_generate)

    orig_build = ta.build_authorization_url

    def spy_build(state, code_challenge, redirect_uri):
        captured["state"] = state
        return orig_build(state, code_challenge, redirect_uri)

    monkeypatch.setattr(ta, "build_authorization_url", spy_build)

    port = _free_port()

    def click_once_state_known():
        for _ in range(50):
            if "state" in captured:
                break
            time.sleep(0.01)
        _click_callback(port, "/callback", f"code=fakecode123&state={captured['state']}", delay=0)

    threading.Thread(target=click_once_state_known, daemon=True).start()

    ta.authorize_interactive(port=port, open_browser=False, timeout_seconds=5)

    # The verifier sent at token exchange must be exactly the one generate_pkce_pair()
    # produced for this attempt, and it must be the correct S256 preimage of the
    # challenge that was actually put on the authorization URL.
    assert sent_verifier["value"] == captured["verifier"]
    import hashlib

    assert hashlib.sha256(captured["verifier"].encode("ascii")).hexdigest() == captured["challenge"]


# ---------------------------------------------------------------------------
# manual two-command fallback
# ---------------------------------------------------------------------------

def test_manual_fallback_requires_redirect_uri(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "")
    with pytest.raises(ta.TikTokAuthError, match="TIKTOK_REDIRECT_URI"):
        ta.start_manual_authorization()


def test_manual_fallback_full_success(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    monkeypatch.setattr(ta.requests, "post", _fake_token_post)

    url = ta.start_manual_authorization()
    assert "code_challenge=" in url
    assert "state=" in url

    pending = ta._load_pending_auth()
    token = ta.complete_manual_authorization("realcode", pending["state"])

    assert token["access_token"] == "access_abc"
    assert ta.load_token()["access_token"] == "access_abc"


def test_manual_fallback_pending_auth_deleted_after_success(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    monkeypatch.setattr(ta.requests, "post", _fake_token_post)

    ta.start_manual_authorization()
    pending = ta._load_pending_auth()
    ta.complete_manual_authorization("realcode", pending["state"])

    assert ta._load_pending_auth() is None


def test_manual_fallback_pending_auth_deleted_even_on_state_mismatch(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    ta.start_manual_authorization()

    with pytest.raises(ta.TikTokAuthError, match="state mismatch"):
        ta.complete_manual_authorization("realcode", "wrong_state")

    assert ta._load_pending_auth() is None  # single-use regardless of outcome


def test_manual_fallback_rejects_state_mismatch(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    ta.start_manual_authorization()

    with pytest.raises(ta.TikTokAuthError, match="state mismatch"):
        ta.complete_manual_authorization("realcode", "wrong_state")


def test_manual_fallback_without_prior_print_auth_url_fails_clearly():
    with pytest.raises(ta.TikTokAuthError, match="No pending authorization"):
        ta.complete_manual_authorization("realcode", "somestate")


def test_manual_fallback_uses_fresh_verifier_per_attempt(monkeypatch):
    monkeypatch.setattr(ta, "TIKTOK_REDIRECT_URI", "https://example.com/callback")
    ta.start_manual_authorization()
    first_verifier = ta._load_pending_auth()["code_verifier"]

    ta.start_manual_authorization()  # a second attempt overwrites the first
    second_verifier = ta._load_pending_auth()["code_verifier"]

    assert first_verifier != second_verifier
