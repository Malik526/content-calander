"""Tests for the hosted Instagram OAuth connection flow (Milestone 4.1):
POST /api/platforms/instagram/connect, GET .../callback, POST .../disconnect
and the identity shown by GET .../status. Every Meta request is mocked at
requests.request; routing, state binding, credential storage and redirects
run for real via TestClient against a temp-file SQLite ContentStore."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from content_automation import config
from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.api.routes import platforms_tiktok
from content_automation.persistence.content_store import ContentStore
from content_automation.publishing import credential_encryption
from content_automation.publishing.instagram import credential_store as ig_cs
from content_automation.publishing.instagram import oauth as ig
from content_automation.publishing.tiktok import auth as tiktok_auth

NOW = "2026-10-06T00:00:00+00:00"
APP_SECRET = "instagram-app-secret-value"
REDIRECT_URI = "https://api.example.com/api/platforms/instagram/callback"
FRONTEND = "https://app.example.com"
SETTINGS = f"{FRONTEND}/app/settings"
EXTRA_TARGET = f"{FRONTEND}/app/settings?tab=platforms"
IG_ACCOUNT_ID = "17841400000000001"
OTHER_IG_ACCOUNT_ID = "17841400000000002"
SECRET_VALUES = ("short-lived-token", "long-lived-token", APP_SECRET, "the-auth-code", IG_ACCOUNT_ID)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeMeta:
    """Mocked Meta endpoints for one account; edit attributes per test."""

    def __init__(self):
        self.user_id = int(IG_ACCOUNT_ID)
        self.username = "pickle.batch"
        self.short = None
        self.long = None
        self.me = None
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    def __call__(self, method, url, *, data=None, params=None, timeout=None):
        path = urlsplit(url).path
        self.calls.append((path, data, params))
        if path == "/oauth/access_token":
            return self.short or FakeResponse(200, {
                "access_token": "short-lived-token", "user_id": self.user_id,
                "permissions": "instagram_business_basic,instagram_business_content_publish",
            })
        if path == "/access_token":
            return self.long or FakeResponse(200, {"access_token": "long-lived-token", "token_type": "bearer", "expires_in": 5_184_000})
        if path.endswith("/me"):
            return self.me or FakeResponse(200, {"user_id": str(self.user_id), "username": self.username})
        raise AssertionError(f"unexpected Meta request: {method} {url}")

    def exchange_calls(self):
        return [call for call in self.calls if call[0] in ("/oauth/access_token", "/access_token")]


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def users(db_path):
    with ContentStore(db_path=db_path) as store:
        return store.create_user("a@example.com", "A", NOW), store.create_user("b@example.com", "B", NOW)


@pytest.fixture
def client(db_path):
    def _override_get_store():
        with ContentStore(db_path=db_path) as s:
            yield s

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_APP_ID", "1234567890123456")
    monkeypatch.setattr(config, "INSTAGRAM_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(config, "INSTAGRAM_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", FRONTEND)
    monkeypatch.setattr(config, "OAUTH_EXTRA_RETURN_TARGETS", [EXTRA_TARGET])
    monkeypatch.setattr(credential_encryption, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


@pytest.fixture(autouse=True)
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(ig.requests, "request", fake)
    return fake


def _act_as(user):
    app_module.app.dependency_overrides[auth_deps.get_current_user] = lambda: user


def _start(client, user, json=None):
    _act_as(user)
    response = client.post("/api/platforms/instagram/connect", json=json)
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]


def _callback(client, **params):
    return client.get("/api/platforms/instagram/callback", params=params, follow_redirects=False)


def _outcome(response):
    """The redirect's query parameters, after checking it lands on the
    Settings page (every target used here shares that path)."""
    parts = urlsplit(response.headers["location"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == SETTINGS
    return parse_qs(parts.query)


def _connect(client, user):
    state = _start(client, user)
    return _callback(client, code="the-auth-code", state=state)


def _connection(db_path, user, platform="instagram"):
    with ContentStore(db_path=db_path) as store:
        connection = store.get_platform_connection(user.id, platform)
        credential = store.get_platform_credential(connection.id) if connection else None
    return connection, credential


def _status(client, user):
    _act_as(user)
    response = client.get("/api/platforms/instagram/status")
    assert response.status_code == 200
    return response


# --- connect ------------------------------------------------------------------------

def test_connect_requires_authentication(client, users):
    assert client.post("/api/platforms/instagram/connect").status_code == 401


def test_connect_returns_the_authorization_url_and_stores_an_owner_bound_state(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)
    before = datetime.now(timezone.utc)

    response = client.post("/api/platforms/instagram/connect")

    assert response.status_code == 200 and set(response.json()) == {"authorization_url"}
    parts = urlsplit(response.json()["authorization_url"])
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.instagram.com/oauth/authorize"
    assert query["client_id"] == ["1234567890123456"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    assert APP_SECRET not in response.text

    with ContentStore(db_path=db_path) as store:
        row = store._conn.execute("SELECT * FROM oauth_states WHERE state = ?", (query["state"][0],)).fetchone()
    assert (row["user_id"], row["platform"], row["code_verifier"], row["redirect_uri"]) == (user_a.id, "instagram", "", REDIRECT_URI)
    assert row["return_target"] == SETTINGS and row["consumed_at"] is None
    ttl = datetime.fromisoformat(row["expires_at"]) - before
    assert timedelta(seconds=config.OAUTH_STATE_TTL_SECONDS - 5) < ttl <= timedelta(seconds=config.OAUTH_STATE_TTL_SECONDS + 5)


def test_each_connect_attempt_gets_a_fresh_state(client, users):
    assert _start(client, users[0]) != _start(client, users[0])


def test_connect_accepts_an_exactly_allowlisted_return_target(client, users, db_path):
    state = _start(client, users[0], json={"return_target": EXTRA_TARGET})
    with ContentStore(db_path=db_path) as store:
        row = store._conn.execute("SELECT return_target FROM oauth_states WHERE state = ?", (state,)).fetchone()
    assert row["return_target"] == EXTRA_TARGET


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example.net/app/settings",
        "//evil.example.net/app/settings",
        "https://app.example.com.evil.net/app/settings",
        "https://app.example.com@evil.net/app/settings",
        "https://app.example.com/app/settings/../../phish",
        "https://app.example.com/app/settings?next=https://evil.example.net",
        "http://app.example.com/app/settings",
        "javascript:alert(1)",
        "picklebatch://oauth",
        "",
    ],
)
def test_connect_rejects_any_unlisted_return_target_and_stores_nothing(client, users, db_path, target):
    _act_as(users[0])
    response = client.post("/api/platforms/instagram/connect", json={"return_target": target})
    assert response.status_code == 400
    assert target not in response.text or target == ""
    with ContentStore(db_path=db_path) as store:
        assert store._conn.execute("SELECT COUNT(*) AS n FROM oauth_states").fetchone()["n"] == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [("INSTAGRAM_APP_ID", ""), ("INSTAGRAM_APP_SECRET", ""), ("INSTAGRAM_REDIRECT_URI", "http://insecure.example.com/cb"),
     ("FRONTEND_BASE_URL", "")],
)
def test_connect_is_unavailable_when_configuration_is_incomplete(client, users, db_path, monkeypatch, name, value):
    monkeypatch.setattr(config, name, value)
    _act_as(users[0])
    response = client.post("/api/platforms/instagram/connect")
    assert response.status_code == 503 and APP_SECRET not in response.text
    assert _status(client, users[0]).json()["connect_available"] is False
    with ContentStore(db_path=db_path) as store:
        assert store._conn.execute("SELECT COUNT(*) AS n FROM oauth_states").fetchone()["n"] == 0


# --- callback: success ----------------------------------------------------------------

def test_callback_connects_the_state_owner_and_stores_only_the_encrypted_long_lived_token(client, users, db_path, meta):
    user_a, _ = users

    response = _connect(client, user_a)

    assert response.status_code == 302 and _outcome(response) == {"instagram": ["connected"]}
    connection, credential = _connection(db_path, user_a)
    assert (connection.status, connection.external_account_id) == ("ACTIVE", IG_ACCOUNT_ID)
    assert "long-lived-token" not in credential.encrypted_payload
    stored = credential_encryption.decrypt_credential(credential.encrypted_payload)
    assert stored["access_token"] == "long-lived-token" and "short-lived-token" not in str(stored)
    assert set(stored) == {"access_token", "token_type", "expires_at", "user_id", "permissions", "obtained_at", "last_refreshed_at"}
    # Exchanged with the exact redirect URI the attempt was started with.
    assert meta.exchange_calls()[0][1]["redirect_uri"] == REDIRECT_URI
    assert meta.exchange_calls()[0][1]["code"] == "the-auth-code"


def test_callback_redirect_and_body_never_carry_credentials(client, users):
    response = _connect(client, users[0])
    exposed = response.headers["location"] + response.text
    assert not any(secret in exposed for secret in SECRET_VALUES)


def test_status_after_connecting_shows_at_username_and_never_the_numeric_id(client, users):
    _connect(client, users[0])
    response = _status(client, users[0])
    assert response.json() == {
        "platform": "instagram", "connected": True, "status": "ACTIVE", "account_label": "@pickle.batch",
        "connect_available": True,
    }
    assert not any(secret in response.text for secret in SECRET_VALUES)


def test_callback_preserves_the_allowlisted_targets_own_query_parameters(client, users):
    state = _start(client, users[0], json={"return_target": EXTRA_TARGET})
    response = _callback(client, code="the-auth-code", state=state)
    assert _outcome(response) == {"tab": ["platforms"], "instagram": ["connected"]}


def test_callback_falls_back_to_settings_when_the_stored_target_is_no_longer_allowlisted(client, users, monkeypatch):
    state = _start(client, users[0], json={"return_target": EXTRA_TARGET})
    monkeypatch.setattr(config, "OAUTH_EXTRA_RETURN_TARGETS", [])
    response = _callback(client, code="the-auth-code", state=state)
    assert response.headers["location"] == f"{SETTINGS}?instagram=connected"


def test_callback_binds_to_the_user_who_started_connect(client, users, db_path):
    user_a, user_b = users
    state = _start(client, user_a)
    _act_as(user_b)  # whoever's browser hits the callback is irrelevant
    _callback(client, code="the-auth-code", state=state)
    assert _connection(db_path, user_a)[0].status == "ACTIVE"
    assert _connection(db_path, user_b)[0] is None
    assert _status(client, user_b).json()["connected"] is False


def test_callback_ignores_identity_hints_in_the_callback_url(client, users, db_path):
    user_a, user_b = users
    state = _start(client, user_a)
    client.get(
        "/api/platforms/instagram/callback",
        params={"code": "the-auth-code", "state": state, "user_id": user_b.id, "return_target": "https://evil.example.net"},
        follow_redirects=False,
    )
    assert _connection(db_path, user_b)[0] is None


def test_connecting_instagram_creates_no_platform_posts(client, users, db_path):
    _connect(client, users[0])
    with ContentStore(db_path=db_path) as store:
        assert store._conn.execute("SELECT COUNT(*) AS n FROM platform_posts").fetchone()["n"] == 0


# --- callback: failures ----------------------------------------------------------------

def test_denied_authorization_redirects_with_denied_and_stores_nothing(client, users, db_path, meta):
    state = _start(client, users[0])
    response = _callback(client, state=state, error="access_denied", error_reason="user_denied", error_description="The user denied")
    assert _outcome(response) == {"instagram": ["denied"]}
    assert "user_denied" not in response.headers["location"]
    assert _connection(db_path, users[0]) == (None, None)
    assert meta.calls == []
    # The attempt is used up.
    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["expired_state"]}


def test_callback_without_code_is_denied(client, users, db_path):
    state = _start(client, users[0])
    assert _outcome(_callback(client, state=state)) == {"instagram": ["denied"]}
    assert _connection(db_path, users[0]) == (None, None)


def test_callback_without_state_is_invalid(client, users, meta):
    assert _outcome(_callback(client, code="the-auth-code")) == {"instagram": ["invalid_state"]}
    assert meta.calls == []


def test_callback_with_unknown_state_is_rejected(client, users, meta):
    assert _outcome(_callback(client, code="the-auth-code", state="never-issued")) == {"instagram": ["expired_state"]}
    assert meta.calls == []


def test_replayed_state_is_rejected_and_exchanges_only_once(client, users, meta):
    state = _start(client, users[0])
    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["connected"]}
    assert _outcome(_callback(client, code="another-code", state=state)) == {"instagram": ["expired_state"]}
    assert len([c for c in meta.calls if c[0] == "/oauth/access_token"]) == 1


def test_expired_state_is_rejected_without_contacting_instagram(client, users, db_path, meta):
    now = datetime.now(timezone.utc)
    with ContentStore(db_path=db_path) as store:
        store.create_oauth_state(
            users[0].id, "instagram", "an-expired-state", "", REDIRECT_URI,
            (now - timedelta(seconds=700)).isoformat(), (now - timedelta(seconds=100)).isoformat(), return_target=SETTINGS,
        )
    assert _outcome(_callback(client, code="the-auth-code", state="an-expired-state")) == {"instagram": ["expired_state"]}
    assert meta.calls == [] and _connection(db_path, users[0]) == (None, None)


def _tiktok_state(db_path, user, state="a-tiktok-state"):
    now = datetime.now(timezone.utc)
    with ContentStore(db_path=db_path) as store:
        store.create_oauth_state(
            user.id, "tiktok", state, "pkce-verifier", "https://api.example.com/api/platforms/tiktok/callback",
            now.isoformat(), (now + timedelta(minutes=10)).isoformat(),
        )
    return state


def _consumed_at(db_path, state):
    with ContentStore(db_path=db_path) as store:
        return store._conn.execute("SELECT consumed_at FROM oauth_states WHERE state = ?", (state,)).fetchone()["consumed_at"]


def test_a_tiktok_state_is_neither_accepted_nor_consumed_by_the_instagram_callback(client, users, db_path, meta, monkeypatch):
    state = _tiktok_state(db_path, users[0])

    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["expired_state"]}
    assert meta.calls == [] and _consumed_at(db_path, state) is None

    # TikTok can still complete its own attempt.
    monkeypatch.setattr(platforms_tiktok, "TIKTOK_WEB_REDIRECT_URI", "https://api.example.com/api/platforms/tiktok/callback")
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: {
        "access_token": "tt-access", "refresh_token": "tt-refresh", "open_id": "open-id",
        "access_token_expires_at": NOW, "refresh_token_expires_at": NOW, "scope": "video.publish",
    })
    tiktok = client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert "tiktok=connected" in tiktok.headers["location"]
    assert _connection(db_path, users[0], "instagram") == (None, None)


def test_an_instagram_state_is_neither_accepted_nor_consumed_by_the_tiktok_callback(client, users, db_path, meta, monkeypatch):
    monkeypatch.setattr(tiktok_auth, "exchange_code_for_token", lambda *a, **k: pytest.fail("TikTok must not exchange"))
    state = _start(client, users[0])

    tiktok = client.get("/api/platforms/tiktok/callback", params={"code": "c", "state": state}, follow_redirects=False)

    assert "tiktok=expired_state" in tiktok.headers["location"]
    assert _consumed_at(db_path, state) is None
    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["connected"]}


@pytest.mark.parametrize(
    ("short", "long"),
    [
        (FakeResponse(200, {"user_id": 1}), None),                                       # no short-lived token
        (FakeResponse(200, {"access_token": "short-lived-token"}), None),                 # no user_id
        (FakeResponse(400, {"error_type": "OAuthException", "code": 400, "error_message": "Invalid code"}), None),
        (FakeResponse(502, None), None),                                                  # non-JSON
        (None, FakeResponse(200, {"access_token": "long-lived-token"})),                  # no expires_in
        (None, FakeResponse(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})),
    ],
)
def test_exchange_failures_redirect_safely_and_store_no_credential(client, users, db_path, meta, short, long):
    meta.short, meta.long = short, long
    state = _start(client, users[0])

    response = _callback(client, code="the-auth-code", state=state)

    assert _outcome(response) == {"instagram": ["exchange_failed"]}
    assert not any(secret in response.headers["location"] + response.text for secret in SECRET_VALUES)
    assert "Invalid" not in response.headers["location"]
    connection, credential = _connection(db_path, users[0])
    assert credential is None and (connection is None or connection.status != "ACTIVE")
    assert _status(client, users[0]).json()["connected"] is False


def test_callback_reports_unavailable_when_the_server_lost_its_configuration(client, users, db_path, meta, monkeypatch):
    state = _start(client, users[0])
    monkeypatch.setattr(config, "INSTAGRAM_APP_SECRET", "")
    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["unavailable"]}
    assert meta.calls == [] and _connection(db_path, users[0]) == (None, None)


def test_callback_reports_unavailable_when_credentials_cannot_be_encrypted(client, users, db_path, monkeypatch):
    state = _start(client, users[0])
    monkeypatch.setattr(credential_encryption, "CREDENTIAL_ENCRYPTION_KEY", "")
    assert _outcome(_callback(client, code="the-auth-code", state=state)) == {"instagram": ["unavailable"]}
    connection, credential = _connection(db_path, users[0])
    assert credential is None and connection.status != "ACTIVE"


# --- reconnect ------------------------------------------------------------------------

def test_reconnecting_a_different_instagram_account_updates_the_connection(client, users, db_path, meta):
    user_a, _ = users
    _connect(client, user_a)
    first_connection, _ = _connection(db_path, user_a)

    meta.user_id, meta.username = int(OTHER_IG_ACCOUNT_ID), "second.account"
    meta.long = FakeResponse(200, {"access_token": "second-long-lived-token", "token_type": "bearer", "expires_in": 5_184_000})
    assert _outcome(_connect(client, user_a)) == {"instagram": ["connected"]}

    connection, credential = _connection(db_path, user_a)
    assert connection.id == first_connection.id
    assert (connection.status, connection.external_account_id) == ("ACTIVE", OTHER_IG_ACCOUNT_ID)
    stored = credential_encryption.decrypt_credential(credential.encrypted_payload)
    assert (stored["access_token"], stored["user_id"]) == ("second-long-lived-token", OTHER_IG_ACCOUNT_ID)
    body = _status(client, user_a)
    assert body.json()["account_label"] == "@second.account" and OTHER_IG_ACCOUNT_ID not in body.text


def test_reconnecting_after_disconnect_reactivates_the_same_connection(client, users, db_path):
    user_a, _ = users
    _connect(client, user_a)
    client.post("/api/platforms/instagram/disconnect")
    _connect(client, user_a)
    connection, credential = _connection(db_path, user_a)
    assert connection.status == "ACTIVE" and credential is not None


# --- identity -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "me",
    [FakeResponse(500, {"error": {"code": 2}}), FakeResponse(200, {"user_id": IG_ACCOUNT_ID}), FakeResponse(200, None),
     FakeResponse(400, {"error": {"message": "Error validating access token", "code": 190}})],
)
def test_identity_failure_keeps_the_connection_without_inventing_a_label(client, users, meta, caplog, me):
    _connect(client, users[0])
    meta.me = me
    with caplog.at_level("INFO"):
        response = _status(client, users[0])
    assert (response.json()["connected"], response.json()["account_label"]) == (True, None)
    assert not any(secret in response.text + caplog.text for secret in SECRET_VALUES)


def test_identity_is_not_looked_up_for_a_user_without_a_connection(client, users, meta):
    assert _status(client, users[0]).json()["account_label"] is None
    assert meta.calls == []


def test_status_refreshes_a_due_token_through_the_shared_credential_store(client, users, db_path, monkeypatch):
    _connect(client, users[0])
    connection, _ = _connection(db_path, users[0])
    old = datetime.now(timezone.utc) - timedelta(days=50)
    with ContentStore(db_path=db_path) as store:
        stored = ig_cs.load_hosted_instagram_token(store, connection.id)
        ig_cs.save_hosted_instagram_token(store, connection.id, {
            **stored, "obtained_at": old.isoformat(), "expires_at": (old + timedelta(days=60)).isoformat(),
        })
    monkeypatch.setattr(ig, "refresh_long_lived_token", lambda token: {
        **token, "access_token": "refreshed-token", "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
    })
    assert _status(client, users[0]).json()["account_label"] == "@pickle.batch"
    with ContentStore(db_path=db_path) as store:
        assert ig_cs.load_hosted_instagram_token(store, connection.id)["access_token"] == "refreshed-token"


# --- disconnect / isolation --------------------------------------------------------------

def test_disconnect_requires_authentication(client, users):
    assert client.post("/api/platforms/instagram/disconnect").status_code == 401


def test_disconnect_removes_the_credential_and_is_idempotent(client, users, db_path):
    user_a, _ = users
    _connect(client, user_a)

    first = client.post("/api/platforms/instagram/disconnect")
    second = client.post("/api/platforms/instagram/disconnect")

    for response in (first, second):
        assert response.status_code == 200
        assert response.json() == {
            "platform": "instagram", "connected": False, "status": "DISCONNECTED", "account_label": None,
            "connect_available": True,
        }
    connection, credential = _connection(db_path, user_a)
    assert connection.status == "DISCONNECTED" and credential is None
    assert _status(client, user_a).json()["connected"] is False


def test_disconnect_without_a_connection_is_a_safe_no_op(client, users, db_path):
    _act_as(users[0])
    assert client.post("/api/platforms/instagram/disconnect").json()["connected"] is False
    assert _connection(db_path, users[0]) == (None, None)


def test_disconnect_touches_only_the_callers_instagram_connection(client, users, db_path):
    user_a, user_b = users
    _connect(client, user_a)
    _connect(client, user_b)
    with ContentStore(db_path=db_path) as store:
        tiktok = store.get_or_create_platform_connection(user_a.id, "tiktok", external_account_id="open-id")
        store.upsert_platform_credential(tiktok.id, "tiktok-ciphertext", NOW)

    _act_as(user_a)
    client.post("/api/platforms/instagram/disconnect")

    assert _connection(db_path, user_a)[0].status == "DISCONNECTED"
    b_connection, b_credential = _connection(db_path, user_b)
    assert b_connection.status == "ACTIVE" and b_credential is not None
    a_tiktok, a_tiktok_credential = _connection(db_path, user_a, "tiktok")
    assert a_tiktok.status == "ACTIVE" and a_tiktok_credential.encrypted_payload == "tiktok-ciphertext"


def test_one_users_connection_is_invisible_to_another(client, users):
    _connect(client, users[0])
    assert _status(client, users[1]).json()["connected"] is False
