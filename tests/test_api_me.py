"""Tests for GET /api/me (Milestone 3.6) — the one route exercised against
REAL token verification (a self-signed RSA keypair standing in for
Supabase's JWKS, same technique as test_token_verification.py) rather than
a dependency override, so the actual auth wiring (bearer header ->
verify_access_token -> resolve_or_create_user -> response) is proven end
to end, not just each piece in isolation."""

import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.identity import token_verification as tv
from content_automation.persistence.content_store import ContentStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def client(db_path):
    # A fresh ContentStore is opened INSIDE the override, per call — not a
    # single store object created once in the fixture and reused. FastAPI
    # runs sync dependencies (get_store, and therefore this override) in a
    # worker thread pool; a sqlite3 connection created in the test's main
    # thread and then reused from a request's worker thread raises
    # "SQLite objects created in a thread can only be used in that same
    # thread." This matches real production behavior exactly — the real
    # get_store() (api/dependencies/auth.py) already opens a fresh store
    # per request for the same reason, so this override is not a test-only
    # workaround, it's what a correct override must do.
    def _override_get_store():
        with ContentStore(db_path=db_path) as s:
            yield s

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def jwks_mode(monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    jwk_dict = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = "test-kid"
    jwk_dict["alg"] = "RS256"
    pyjwk = PyJWK.from_json(json.dumps(jwk_dict))

    class _FakeClient:
        def get_signing_key_from_jwt(self, token):
            return pyjwk

    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "_jwks_client", lambda: _FakeClient())


def _token(private_key, **claims):
    payload = {"sub": "google-sub-1", "email": "creator@example.com", "aud": "authenticated", **claims}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})


def test_get_me_with_a_real_valid_token_creates_and_returns_the_user(client, rsa_keypair, db_path):
    private_key, _ = rsa_keypair
    token = _token(private_key)

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "creator@example.com"
    # A real users/auth_identities row was actually created (verified via
    # a separate connection to the same on-disk db), not just a response
    # shaped as if it were.
    with ContentStore(db_path=db_path) as verify_store:
        assert verify_store.get_user_by_auth_identity("supabase", "google-sub-1") is not None


def test_get_me_second_request_resolves_the_same_user(client, rsa_keypair):
    private_key, _ = rsa_keypair
    token = _token(private_key)

    first = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    second = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()

    assert first["id"] == second["id"]


def test_get_me_without_authorization_header_is_rejected(client):
    response = client.get("/api/me")
    assert response.status_code == 401


def test_get_me_with_malformed_authorization_header_is_rejected(client):
    response = client.get("/api/me", headers={"Authorization": "NotBearer sometoken"})
    assert response.status_code == 401


def test_get_me_with_an_invalid_signature_is_rejected(client, rsa_keypair):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"sub": "google-sub-1", "aud": "authenticated"}, other_key, algorithm="RS256", headers={"kid": "test-kid"})

    response = client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_response_never_includes_credential_shaped_fields(client, rsa_keypair):
    private_key, _ = rsa_keypair
    token = _token(private_key)

    body = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()

    for forbidden in ("access_token", "refresh_token", "password", "token"):
        assert forbidden not in body
