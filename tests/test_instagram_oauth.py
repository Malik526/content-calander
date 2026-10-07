"""Tests for publishing.instagram.oauth (Milestone 4.1): the Instagram Login
wire protocol — authorization URL, code → short-lived → long-lived
exchange, refresh, profile lookup — with every Meta request mocked
(requests.request). Also proves errors never carry tokens, codes, the app
secret or response bodies."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from content_automation import config
from content_automation.publishing.instagram import oauth as ig

APP_SECRET = "app-secret-must-never-leak"
REDIRECT_URI = "https://api.example.com/api/platforms/instagram/callback"


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeMeta:
    """Routes mocked requests by URL path suffix; records every call."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.calls: list[dict] = []

    def on(self, path_suffix, response):
        self.routes[path_suffix] = response

    def __call__(self, method, url, *, data=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data, "params": params, "timeout": timeout})
        for suffix, response in self.routes.items():
            if urlsplit(url).path.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected Meta request: {method} {url}")


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_APP_ID", "1234567890123456")
    monkeypatch.setattr(config, "INSTAGRAM_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(config, "INSTAGRAM_REDIRECT_URI", REDIRECT_URI)


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(ig.requests, "request", fake)
    return fake


def _successful_exchange(meta, *, user_id=17841400000000001, wrapped=False, permissions="instagram_business_basic,instagram_business_content_publish"):
    short = {"access_token": "short-lived-token", "user_id": user_id, "permissions": permissions}
    meta.on("/oauth/access_token", FakeResponse(200, {"data": [short]} if wrapped else short))
    meta.on("/access_token", FakeResponse(200, {"access_token": "long-lived-token", "token_type": "bearer", "expires_in": 5_184_000}))


def _all_text(exc: BaseException) -> str:
    """The exception's message plus every exception a traceback would print
    alongside it (explicit cause, or unsuppressed context)."""
    text = f"{exc} {exc!r}"
    if exc.__cause__ is not None:
        text += _all_text(exc.__cause__)
    elif exc.__context__ is not None and not exc.__suppress_context__:
        text += _all_text(exc.__context__)
    return text


# --- authorization URL --------------------------------------------------------

def test_authorization_url_carries_exactly_the_documented_parameters():
    url = ig.build_authorization_url("state-123", REDIRECT_URI)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.instagram.com/oauth/authorize"
    assert parse_qs(parts.query) == {
        "client_id": ["1234567890123456"],
        "redirect_uri": [REDIRECT_URI],
        "response_type": ["code"],
        "scope": ["instagram_business_basic,instagram_business_content_publish"],
        "state": ["state-123"],
    }
    assert "code_challenge" not in url and APP_SECRET not in url


def test_authorization_url_requires_an_app_id(monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_APP_ID", "")
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.build_authorization_url("s", REDIRECT_URI)
    assert error.value.reason_code == "NOT_CONFIGURED"


def test_states_are_fresh_and_unguessable():
    states = {ig.generate_state() for _ in range(50)}
    assert len(states) == 50 and all(len(s) >= 32 for s in states)


# --- code exchange --------------------------------------------------------------

@pytest.mark.parametrize("wrapped", [False, True])
def test_code_exchange_returns_only_the_long_lived_credential(meta, wrapped):
    _successful_exchange(meta, wrapped=wrapped)
    before = datetime.now(timezone.utc)

    token = ig.exchange_code_for_long_lived_token("auth-code", REDIRECT_URI)

    assert token["access_token"] == "long-lived-token"
    assert "short-lived-token" not in str(token)
    assert token["user_id"] == "17841400000000001"
    assert token["permissions"] == ["instagram_business_basic", "instagram_business_content_publish"]
    assert token["token_type"] == "bearer" and token["last_refreshed_at"] is None
    expires_at = datetime.fromisoformat(token["expires_at"])
    assert before + timedelta(days=59) < expires_at < before + timedelta(days=61)

    short_call, long_call = meta.calls
    assert (short_call["method"], short_call["url"]) == ("POST", "https://api.instagram.com/oauth/access_token")
    assert short_call["data"] == {
        "client_id": "1234567890123456", "client_secret": APP_SECRET, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI, "code": "auth-code",
    }
    assert (long_call["method"], long_call["url"]) == ("GET", "https://graph.instagram.com/access_token")
    assert long_call["params"] == {"grant_type": "ig_exchange_token", "client_secret": APP_SECRET, "access_token": "short-lived-token"}


def test_code_exchange_strips_metas_trailing_fragment_marker(meta):
    _successful_exchange(meta)
    ig.exchange_code_for_long_lived_token("auth-code#_", REDIRECT_URI)
    assert meta.calls[0]["data"]["code"] == "auth-code"


def test_permissions_given_as_a_list_are_normalized(meta):
    _successful_exchange(meta, permissions=["instagram_business_basic", " instagram_business_content_publish "])
    token = ig.exchange_code_for_long_lived_token("c", REDIRECT_URI)
    assert token["permissions"] == ["instagram_business_basic", "instagram_business_content_publish"]


def test_code_exchange_requires_the_app_secret(meta, monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_APP_SECRET", "")
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("c", REDIRECT_URI)
    assert error.value.reason_code == "NOT_CONFIGURED" and meta.calls == []


@pytest.mark.parametrize(
    "short_body",
    [
        {"user_id": 1},                                    # no access_token
        {"access_token": "", "user_id": 1},                # empty access_token
        {"access_token": "short-lived-token"},             # no user_id
        {"access_token": "short-lived-token", "user_id": "not-a-number"},
        {"access_token": "short-lived-token", "user_id": True},
        {"data": []},
    ],
)
def test_malformed_short_lived_responses_are_rejected_without_echoing_them(meta, short_body):
    meta.on("/oauth/access_token", FakeResponse(200, short_body))
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("auth-code", REDIRECT_URI)
    assert error.value.reason_code == "MALFORMED_RESPONSE"
    assert "short-lived-token" not in _all_text(error.value) and "auth-code" not in _all_text(error.value)
    assert len(meta.calls) == 1  # never went on to the long-lived exchange


@pytest.mark.parametrize(
    "long_body",
    [
        {"token_type": "bearer", "expires_in": 5_184_000},                       # no access_token
        {"access_token": "long-lived-token", "token_type": "bearer"},            # no expires_in
        {"access_token": "long-lived-token", "expires_in": -1},
        {"access_token": "long-lived-token", "expires_in": "soon"},
    ],
)
def test_malformed_long_lived_responses_are_rejected(meta, long_body):
    meta.on("/oauth/access_token", FakeResponse(200, {"access_token": "short-lived-token", "user_id": 1}))
    meta.on("/access_token", FakeResponse(200, long_body))
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("c", REDIRECT_URI)
    assert error.value.reason_code == "MALFORMED_RESPONSE"
    assert "long-lived-token" not in _all_text(error.value)


def test_non_json_response_is_malformed_and_its_body_is_not_echoed(meta):
    meta.on("/oauth/access_token", FakeResponse(502, None, text="<html>secret upstream page</html>"))
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("c", REDIRECT_URI)
    assert (error.value.reason_code, error.value.http_status) == ("MALFORMED_RESPONSE", 502)
    assert "secret upstream page" not in _all_text(error.value)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error_type": "OAuthException", "code": 400, "error_message": "Invalid authorization code"}),
        (200, {"error": {"message": "Invalid platform app", "type": "OAuthException", "code": 101}}),
    ],
)
def test_rejected_code_exchange_is_a_structured_error_without_provider_text(meta, status, body):
    meta.on("/oauth/access_token", FakeResponse(status, body))
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("auth-code", REDIRECT_URI)
    assert error.value.reason_code == "AUTH_HTTP_ERROR"
    assert error.value.provider_error_code in (400, 101)
    text = _all_text(error.value)
    assert "Invalid" not in text and "auth-code" not in text and APP_SECRET not in text


def test_network_failure_never_leaks_the_secret_or_token_in_the_url(meta):
    # requests' own messages include the request URL — which, for the
    # long-lived exchange, carries the app secret and the short-lived token.
    meta.on("/oauth/access_token", FakeResponse(200, {"access_token": "short-lived-token", "user_id": 1}))
    meta.on("/access_token", requests.ConnectionError(
        f"Max retries exceeded with url: /access_token?client_secret={APP_SECRET}&access_token=short-lived-token"
    ))
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.exchange_code_for_long_lived_token("c", REDIRECT_URI)
    assert error.value.reason_code == "NETWORK_ERROR"
    assert error.value.__cause__ is None and error.value.__suppress_context__
    text = _all_text(error.value)
    assert APP_SECRET not in text and "short-lived-token" not in text


# --- refresh --------------------------------------------------------------------

def _stored(**overrides):
    now = datetime.now(timezone.utc)
    return {
        "access_token": "long-lived-token", "token_type": "bearer",
        "expires_at": (now + timedelta(days=10)).isoformat(), "user_id": "17841400000000001",
        "permissions": ["instagram_business_basic"], "obtained_at": (now - timedelta(days=50)).isoformat(),
        "last_refreshed_at": None, **overrides,
    }


def test_refresh_replaces_the_token_and_expiry_and_keeps_the_account(meta):
    meta.on("/refresh_access_token", FakeResponse(200, {"access_token": "refreshed-token", "token_type": "bearer", "expires_in": 5_184_000}))
    stored = _stored()

    refreshed = ig.refresh_long_lived_token(stored)

    assert refreshed["access_token"] == "refreshed-token"
    assert datetime.fromisoformat(refreshed["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=59)
    assert refreshed["last_refreshed_at"] is not None
    assert (refreshed["user_id"], refreshed["obtained_at"], refreshed["permissions"]) == (
        stored["user_id"], stored["obtained_at"], stored["permissions"],
    )
    call = meta.calls[0]
    assert (call["method"], call["url"]) == ("GET", "https://graph.instagram.com/refresh_access_token")
    assert call["params"] == {"grant_type": "ig_refresh_token", "access_token": "long-lived-token"}
    assert APP_SECRET not in str(call)  # refresh needs no app secret


def test_a_rejected_refresh_requires_reauthorization(meta):
    meta.on("/refresh_access_token", FakeResponse(400, {"error": {"message": "Error validating access token", "code": 190}}))
    with pytest.raises(ig.InstagramReauthorizationRequiredError) as error:
        ig.refresh_long_lived_token(_stored())
    assert error.value.reason_code == "REAUTHORIZATION_REQUIRED" and error.value.provider_error_code == 190
    assert "long-lived-token" not in _all_text(error.value) and "validating" not in _all_text(error.value)


@pytest.mark.parametrize("failure", [FakeResponse(503, {"error": {"code": 2}}), requests.Timeout("timed out")])
def test_a_transient_refresh_failure_stays_retryable(meta, failure):
    meta.on("/refresh_access_token", failure)
    with pytest.raises(ig.InstagramAuthError) as error:
        ig.refresh_long_lived_token(_stored())
    assert not isinstance(error.value, ig.InstagramReauthorizationRequiredError)


# --- profile --------------------------------------------------------------------

@pytest.mark.parametrize("wrapped", [False, True])
def test_profile_lookup_returns_username_and_account_id(meta, wrapped):
    body = {"user_id": "17841400000000001", "username": "pickle.batch", "id": "app-scoped"}
    meta.on("/me", FakeResponse(200, {"data": [body]} if wrapped else body))

    profile = ig.fetch_account_profile("long-lived-token", timeout=5)

    assert profile == ig.InstagramProfile(user_id="17841400000000001", username="pickle.batch")
    call = meta.calls[0]
    assert call["url"] == f"https://graph.instagram.com/{config.INSTAGRAM_GRAPH_API_VERSION}/me"
    assert call["params"] == {"fields": "user_id,username", "access_token": "long-lived-token"}
    assert call["timeout"] == 5


@pytest.mark.parametrize("username", [None, "", "   ", "has space", "<script>", "x" * 31, 42])
def test_missing_or_invalid_usernames_are_not_reported(meta, username):
    meta.on("/me", FakeResponse(200, {"user_id": 1, "username": username}))
    assert ig.fetch_account_profile("t").username is None
