"""Tests for /api/platforms/tiktok/* (Milestone 3.6): the hosted TikTok
OAuth connection flow, tenant isolation, and OAuth security properties.
Network access to TikTok itself (exchange_code_for_token) is mocked; the
FastAPI routing/state-binding/credential-storage logic is exercised for
real via TestClient against a real temp-file SQLite ContentStore."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.api.routes import platforms_tiktok
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.tiktok import auth as tiktok_auth

FAKE_WEB_REDIRECT_URI = "https://api.example.com/api/platforms/tiktok/callback"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def users(db_path):
    """Two real, pre-created users — A and B — used throughout to prove
    tenant isolation directly."""
    with ContentStore(db_path=db_path) as store:
        user_a = store.create_user("a@example.com", "User A", "2026-01-01T00:00:00+00:00")
        user_b = store.create_user("b@example.com", "User B", "2026-01-01T00:00:00+00:00")
    return user_a, user_b


@pytest.fixture
def client(db_path):
    def _override_get_store():
        with ContentStore(db_path=db_path) as s:
            yield s

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _act_as(user):
    """Reassigns the get_current_user override mid-test — lets a single
    test authenticate as different users across separate requests."""
    app_module.app.dependency_overrides[auth_deps.get_current_user] = lambda: user


def _fake_token(open_id="tiktok_open_id_1"):
    now = datetime.now(timezone.utc)
    return {
        "access_token": "access_abc",
        "refresh_token": "refresh_xyz",
        "access_token_expires_at": (now + timedelta(hours=1)).isoformat(),
        "refresh_token_expires_at": (now + timedelta(days=300)).isoformat(),
        "open_id": open_id,
        "scope": "user.info.basic,video.publish",
    }


@pytest.fixture(autouse=True)
def credential_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    from content_automation.publishing.tiktok import credential_store as cs

    monkeypatch.setattr(cs, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


@pytest.fixture(autouse=True)
def tiktok_web_redirect_uri(monkeypatch):
    """A fixed, valid TIKTOK_WEB_REDIRECT_URI for every test by default —
    the hosted connect flow now fails closed without one (see
    test_connect_fails_closed_* below for that behavior itself), so every
    other test needs a real value to exercise the connect/callback flow
    at all."""
    monkeypatch.setattr(platforms_tiktok, "TIKTOK_WEB_REDIRECT_URI", FAKE_WEB_REDIRECT_URI)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_when_never_connected(client, users):
    user_a, _ = users
    _act_as(user_a)

    response = client.get("/api/platforms/tiktok/status")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["status"] == "DISCONNECTED"


def test_status_requires_authentication(client, users):
    app_module.app.dependency_overrides.pop(auth_deps.get_current_user, None)
    response = client.get("/api/platforms/tiktok/status")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# connect -> callback (full flow)
# ---------------------------------------------------------------------------

def test_connect_returns_a_real_tiktok_authorization_url(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")

    response = client.post("/api/platforms/tiktok/connect")

    assert response.status_code == 200
    url = response.json()["authorization_url"]
    assert "code_challenge=" in url
    assert "state=" in url


def test_full_connect_and_callback_flow_activates_the_connection(client, users, monkeypatch, db_path):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token())

    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]

    callback_response = client.get(
        "/api/platforms/tiktok/callback", params={"code": "real_code", "state": state}, follow_redirects=False,
    )

    assert callback_response.status_code == 302
    assert "tiktok=connected" in callback_response.headers["location"]

    status_response = client.get("/api/platforms/tiktok/status")
    body = status_response.json()
    assert body["connected"] is True
    assert body["status"] == "ACTIVE"
    # Milestone 3.6 security review: account_label is never the raw TikTok
    # open_id (an opaque internal identifier, not a real label) — it stays
    # None until a real display name is available. See test below for
    # proof that per-user open_id association still happens correctly at
    # the persistence layer, just isn't surfaced through this field.
    assert body["account_label"] is None


def test_callback_never_returns_the_access_or_refresh_token(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token())
    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]

    status_body = client.get("/api/platforms/tiktok/status").json()
    callback_response = client.get(
        "/api/platforms/tiktok/callback", params={"code": "real_code", "state": state}, follow_redirects=False,
    )

    for body in (status_body,):
        assert "access_token" not in body
        assert "refresh_token" not in body
    # The callback itself is a bare redirect — no response body at all,
    # let alone one carrying a token.
    assert callback_response.text == ""


# ---------------------------------------------------------------------------
# Explicit web redirect_uri configuration (Milestone 3.6 correction) — the
# hosted flow's redirect_uri used to be derived from request.url_for(),
# which can silently drift from whatever's actually registered in TikTok's
# Developer Portal behind a reverse proxy. It's now TIKTOK_WEB_REDIRECT_URI,
# used identically for authorization-URL generation and callback token
# exchange, validated at connect-time.
# ---------------------------------------------------------------------------

def test_connect_uses_the_configured_web_redirect_uri(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")

    response = client.post("/api/platforms/tiktok/connect")

    assert response.status_code == 200
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(response.json()["authorization_url"]).query)
    assert query["redirect_uri"][0] == FAKE_WEB_REDIRECT_URI


def test_callback_token_exchange_uses_the_same_configured_redirect_uri(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    captured = {}

    def _capture_exchange(code, code_verifier, redirect_uri):
        captured["redirect_uri"] = redirect_uri
        return _fake_token()

    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", _capture_exchange)
    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]

    client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    assert captured["redirect_uri"] == FAKE_WEB_REDIRECT_URI


def test_connect_ignores_the_incoming_request_host_and_scheme(client, users, monkeypatch):
    """Proves the redirect_uri no longer comes from the request at all —
    a request that looks like it arrived via a completely different
    host/scheme (as if behind a proxy, or a spoofed Host header) still
    produces the one configured redirect_uri, never something derived
    from what the request claims about itself."""
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")

    response = client.post(
        "/api/platforms/tiktok/connect",
        headers={"host": "attacker-controlled.example.net", "x-forwarded-proto": "http"},
    )

    assert response.status_code == 200
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(response.json()["authorization_url"]).query)
    assert query["redirect_uri"][0] == FAKE_WEB_REDIRECT_URI


def test_connect_fails_closed_when_web_redirect_uri_is_not_configured(client, users, monkeypatch, db_path):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(platforms_tiktok, "TIKTOK_WEB_REDIRECT_URI", "")

    response = client.post("/api/platforms/tiktok/connect")

    assert response.status_code == 500
    assert "TIKTOK_WEB_REDIRECT_URI" in response.json()["detail"]
    # No oauth_states row was persisted from the failed attempt — the
    # validation happens before any TikTok call or state write.
    import sqlite3

    with sqlite3.connect(db_path) as raw_conn:
        (count,) = raw_conn.execute("SELECT COUNT(*) FROM oauth_states").fetchone()
    assert count == 0


def test_connect_fails_closed_when_web_redirect_uri_is_not_https(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(platforms_tiktok, "TIKTOK_WEB_REDIRECT_URI", "http://insecure.example.com/callback")

    response = client.post("/api/platforms/tiktok/connect")

    assert response.status_code == 500
    assert "https" in response.json()["detail"]


def test_desktop_redirect_uri_does_not_configure_the_hosted_web_flow(client, users, monkeypatch):
    """TIKTOK_REDIRECT_URI (the separate local desktop/CLI flow's variable
    — see publishing/tiktok/auth.py) has no effect on the hosted flow: the
    hosted connect endpoint reads only config.TIKTOK_WEB_REDIRECT_URI, so
    setting the desktop variable alone must not satisfy it. Regression
    guard for the config.py TIKTOK_REDIRECT_URI/TIKTOK_WEB_REDIRECT_URI
    naming confusion (see CHANGELOG.md)."""
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_REDIRECT_URI", "https://example.com/some/desktop/callback")
    monkeypatch.setattr(platforms_tiktok, "TIKTOK_WEB_REDIRECT_URI", "")

    response = client.post("/api/platforms/tiktok/connect")

    assert response.status_code == 500
    assert "TIKTOK_WEB_REDIRECT_URI" in response.json()["detail"]


# ---------------------------------------------------------------------------
# OAuth security (Phase 23)
# ---------------------------------------------------------------------------

def test_callback_denied_by_tiktok_redirects_with_denied_reason(client):
    response = client.get(
        "/api/platforms/tiktok/callback",
        params={"error": "access_denied", "error_description": "user declined"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "tiktok=denied" in response.headers["location"]
    assert "access_denied" not in response.headers["location"]  # no raw TikTok detail leaked


def test_callback_with_unknown_state_is_rejected(client):
    response = client.get(
        "/api/platforms/tiktok/callback", params={"code": "c", "state": "never-issued"}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert "tiktok=expired_state" in response.headers["location"]


def test_callback_state_replay_is_rejected(client, users, monkeypatch):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token())
    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]
    first = client.get("/api/platforms/tiktok/callback", params={"code": "c1", "state": state}, follow_redirects=False)
    assert "tiktok=connected" in first.headers["location"]

    replay = client.get("/api/platforms/tiktok/callback", params={"code": "c2", "state": state}, follow_redirects=False)

    assert "tiktok=expired_state" in replay.headers["location"]


def test_callback_expired_state_is_rejected(client, users, db_path):
    user_a, _ = users
    now = datetime.now(timezone.utc)
    with ContentStore(db_path=db_path) as store:
        store.create_oauth_state(
            user_a.id, "tiktok", "an-expired-state", "verifier", "http://testserver/api/platforms/tiktok/callback",
            (now - timedelta(seconds=700)).isoformat(), (now - timedelta(seconds=100)).isoformat(),
        )

    response = client.get(
        "/api/platforms/tiktok/callback", params={"code": "c", "state": "an-expired-state"}, follow_redirects=False,
    )

    assert "tiktok=expired_state" in response.headers["location"]


def test_callback_binds_to_the_user_who_initiated_connect_regardless_of_who_hits_the_callback(
    client, users, monkeypatch,
):
    """The callback endpoint is unauthenticated by necessity (TikTok
    redirects the browser directly) — proves the resulting connection is
    still attributed to whichever user actually STARTED the flow (the
    oauth_states row's own user_id), never to whatever the current
    request's session happens to be, since state binding — not a header —
    is what determines ownership here."""
    user_a, user_b = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token())
    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]

    # Switch "current session" to user B before the callback fires — as if
    # B's browser somehow hit A's callback URL. The callback route takes
    # no auth dependency at all, so this only proves the route doesn't
    # accidentally consult get_current_user for attribution.
    _act_as(user_b)
    client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    _act_as(user_a)
    a_status = client.get("/api/platforms/tiktok/status").json()
    _act_as(user_b)
    b_status = client.get("/api/platforms/tiktok/status").json()

    assert a_status["connected"] is True
    assert b_status["connected"] is False


# ---------------------------------------------------------------------------
# tenant isolation (Phase 21)
# ---------------------------------------------------------------------------

def test_two_users_have_completely_independent_connections(client, users, db_path, monkeypatch):
    user_a, user_b = users
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")

    def _connect_as(user, open_id):
        _act_as(user)
        monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token(open_id))
        connect_response = client.post("/api/platforms/tiktok/connect")
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]
        client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    _connect_as(user_a, "open_id_a")
    _connect_as(user_b, "open_id_b")

    _act_as(user_a)
    a_status = client.get("/api/platforms/tiktok/status").json()
    _act_as(user_b)
    b_status = client.get("/api/platforms/tiktok/status").json()

    assert a_status["connected"] is True
    assert b_status["connected"] is True

    # account_label is deliberately never the raw open_id (see the security
    # review above this test file's other account_label assertions), so
    # isolation of *which TikTok account* each user is bound to is proven
    # directly at the persistence layer instead — each user's own
    # external_account_id must be theirs, not the other user's.
    with ContentStore(db_path=db_path) as store:
        connection_a = store.get_platform_connection(user_a.id, "tiktok")
        connection_b = store.get_platform_connection(user_b.id, "tiktok")
    assert connection_a.external_account_id == "open_id_a"
    assert connection_b.external_account_id == "open_id_b"


def test_disconnecting_one_user_never_affects_the_other(client, users, monkeypatch):
    user_a, user_b = users
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")

    for user, open_id in ((user_a, "open_id_a"), (user_b, "open_id_b")):
        _act_as(user)
        monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, oid=open_id, **k: _fake_token(oid))
        connect_response = client.post("/api/platforms/tiktok/connect")
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]
        client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    _act_as(user_a)
    client.post("/api/platforms/tiktok/disconnect")

    _act_as(user_a)
    a_status = client.get("/api/platforms/tiktok/status").json()
    _act_as(user_b)
    b_status = client.get("/api/platforms/tiktok/status").json()

    assert a_status["connected"] is False
    assert b_status["connected"] is True


def test_disconnect_with_no_existing_connection_is_a_safe_no_op(client, users):
    user_a, _ = users
    _act_as(user_a)

    response = client.post("/api/platforms/tiktok/disconnect")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_disconnect_removes_the_stored_credential(client, users, monkeypatch, db_path):
    user_a, _ = users
    _act_as(user_a)
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_KEY", "fake_key")
    monkeypatch.setattr(tiktok_auth, "TIKTOK_CLIENT_SECRET", "fake_secret")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: _fake_token())
    connect_response = client.post("/api/platforms/tiktok/connect")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect_response.json()["authorization_url"]).query)["state"][0]
    client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    client.post("/api/platforms/tiktok/disconnect")

    with ContentStore(db_path=db_path) as verify_store:
        connection = verify_store.get_platform_connection(user_a.id, "tiktok")
        assert connection.status == "DISCONNECTED"
        assert verify_store.get_platform_credential(connection.id) is None
