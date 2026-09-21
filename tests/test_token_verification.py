"""Tests for identity.token_verification (Milestone 3.6).

No live Supabase project is needed: a real RSA keypair is generated
locally with `cryptography`, a real JWT is signed with PyJWT against the
private key, and the module's own internal JWKS-client cache
(_jwks_client) is monkeypatched to return that keypair's public JWK
directly — proving the actual signature-verification code path works
against a real (self-issued) JWKS shape, not a mocked-away assertion."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm

from content_automation.identity import token_verification as tv


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign(private_key, claims: dict, *, kid: str = "test-kid") -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _fake_jwks_client(public_key, kid: str = "test-kid"):
    jwk_dict = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = kid
    jwk_dict["alg"] = "RS256"
    pyjwk = PyJWK.from_json(__import__("json").dumps(jwk_dict))

    class _FakeClient:
        def get_signing_key_from_jwt(self, token):
            return pyjwk

    return _FakeClient()


@pytest.fixture(autouse=True)
def reset_jwks_cache():
    tv._jwks_client.cache_clear()
    yield
    tv._jwks_client.cache_clear()


def test_verify_access_token_accepts_a_real_valid_rs256_token(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "_jwks_client", lambda: _fake_jwks_client(public_key))
    token = _sign(private_key, {"sub": "user-abc", "email": "a@example.com", "aud": "authenticated"})

    identity = tv.verify_access_token(token)

    assert identity.subject == "user-abc"
    assert identity.email == "a@example.com"
    assert identity.raw_claims["aud"] == "authenticated"


def test_verify_access_token_rejects_expired_token(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "_jwks_client", lambda: _fake_jwks_client(public_key))
    token = _sign(
        private_key,
        {"sub": "user-abc", "aud": "authenticated", "exp": int(time.time()) - 60},
    )

    with pytest.raises(tv.TokenVerificationError):
        tv.verify_access_token(token)


def test_verify_access_token_rejects_wrong_signing_key(monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    other_private_key, _ = (lambda k: (k, k.public_key()))(
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    # JWKS client always returns the FIRST keypair's public key — but the
    # token is signed with a DIFFERENT private key, simulating a forged
    # or tampered token.
    monkeypatch.setattr(tv, "_jwks_client", lambda: _fake_jwks_client(public_key))
    forged_token = _sign(other_private_key, {"sub": "user-abc", "aud": "authenticated"})

    with pytest.raises(tv.TokenVerificationError):
        tv.verify_access_token(forged_token)


def test_verify_access_token_rejects_wrong_audience(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "_jwks_client", lambda: _fake_jwks_client(public_key))
    token = _sign(private_key, {"sub": "user-abc", "aud": "some-other-audience"})

    with pytest.raises(tv.TokenVerificationError):
        tv.verify_access_token(token)


def test_verify_access_token_rejects_missing_sub_claim(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "_jwks_client", lambda: _fake_jwks_client(public_key))
    token = _sign(private_key, {"aud": "authenticated"})

    with pytest.raises(tv.TokenVerificationError, match="sub"):
        tv.verify_access_token(token)


def test_hs256_mode_verifies_with_the_shared_secret(monkeypatch):
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "hs256")
    monkeypatch.setattr(tv, "SUPABASE_JWT_SECRET", "a-real-shared-secret")
    token = jwt.encode({"sub": "user-xyz", "aud": "authenticated"}, "a-real-shared-secret", algorithm="HS256")

    identity = tv.verify_access_token(token)

    assert identity.subject == "user-xyz"


def test_hs256_mode_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "hs256")
    monkeypatch.setattr(tv, "SUPABASE_JWT_SECRET", "the-real-secret")
    token = jwt.encode({"sub": "user-xyz", "aud": "authenticated"}, "a-forged-secret", algorithm="HS256")

    with pytest.raises(tv.TokenVerificationError):
        tv.verify_access_token(token)


def test_hs256_mode_without_secret_configured_fails_closed(monkeypatch):
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "hs256")
    monkeypatch.setattr(tv, "SUPABASE_JWT_SECRET", "")

    with pytest.raises(tv.TokenVerificationError, match="SUPABASE_JWT_SECRET"):
        tv.verify_access_token("irrelevant-token-value")


def test_jwks_mode_without_url_configured_fails_closed(monkeypatch):
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "jwks")
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWKS_URL", "")

    with pytest.raises(tv.TokenVerificationError):
        tv.verify_access_token("irrelevant-token-value")


def test_unknown_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(tv, "SUPABASE_AUTH_JWT_MODE", "some-unsupported-mode")

    with pytest.raises(tv.TokenVerificationError, match="Unknown SUPABASE_AUTH_JWT_MODE"):
        tv.verify_access_token("irrelevant-token-value")
