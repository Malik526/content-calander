"""Tests for publishing.tiktok.credential_store (Milestone 3.6): hosted,
per-connection, encrypted TikTok credential storage and the CAS-based
hosted refresh path. Network access (requests.post, via tiktok_auth) is
mocked — no real TikTok credentials required."""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from content_automation.persistence.content_store import ContentStore
from content_automation.publishing.tiktok import auth as tiktok_auth
from content_automation.publishing.tiktok import credential_store as cs


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def connection_id(store):
    user = store.get_or_create_local_user()
    conn = store.get_or_create_platform_connection(user.id, "tiktok")
    return conn.id


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(cs, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


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


def _expired_token(now=None):
    now = now or datetime.now(timezone.utc)
    return _valid_token(now, access_ttl_minutes=-10)


# ---------------------------------------------------------------------------
# encryption
# ---------------------------------------------------------------------------

def test_encrypt_then_decrypt_roundtrips():
    token = _valid_token()
    ciphertext = cs.encrypt_token(token)
    assert ciphertext != str(token)
    assert cs.decrypt_token(ciphertext) == token


def test_encrypt_without_key_configured_fails_closed(monkeypatch):
    monkeypatch.setattr(cs, "CREDENTIAL_ENCRYPTION_KEY", "")
    with pytest.raises(cs.CredentialStoreError, match="CREDENTIAL_ENCRYPTION_KEY"):
        cs.encrypt_token(_valid_token())


def test_decrypt_with_wrong_key_fails_closed(monkeypatch):
    ciphertext = cs.encrypt_token(_valid_token())
    monkeypatch.setattr(cs, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    with pytest.raises(cs.CredentialStoreError, match="could not be decrypted"):
        cs.decrypt_token(ciphertext)


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

def test_load_returns_none_when_nothing_saved(store, connection_id):
    assert cs.load_hosted_tiktok_token(store, connection_id) is None


def test_save_then_load_roundtrips(store, connection_id):
    token = _valid_token()
    cs.save_hosted_tiktok_token(store, connection_id, token)
    assert cs.load_hosted_tiktok_token(store, connection_id) == token


def test_saving_twice_overwrites_unconditionally(store, connection_id):
    cs.save_hosted_tiktok_token(store, connection_id, _valid_token())
    second = _valid_token()
    second["access_token"] = "a-second-connect-attempt-token"
    cs.save_hosted_tiktok_token(store, connection_id, second)
    assert cs.load_hosted_tiktok_token(store, connection_id)["access_token"] == "a-second-connect-attempt-token"


def test_credential_is_never_stored_as_plaintext(store, connection_id):
    cs.save_hosted_tiktok_token(store, connection_id, _valid_token())
    record = store.get_platform_credential(connection_id)
    assert "access_abc" not in record.encrypted_payload
    assert "refresh_xyz" not in record.encrypted_payload


# ---------------------------------------------------------------------------
# get_hosted_tiktok_access_token
# ---------------------------------------------------------------------------

def test_returns_the_cached_token_without_refreshing_when_still_valid(store, connection_id, monkeypatch):
    cs.save_hosted_tiktok_token(store, connection_id, _valid_token())
    monkeypatch.setattr(
        tiktok_auth, "refresh_access_token", lambda *a, **k: pytest.fail("should not refresh a valid token")
    )
    assert cs.get_hosted_tiktok_access_token(store, connection_id) == "access_abc"


def test_raises_reauthorization_required_when_nothing_saved(store, connection_id):
    with pytest.raises(tiktok_auth.TikTokReauthorizationRequiredError):
        cs.get_hosted_tiktok_access_token(store, connection_id)


def test_refreshes_and_persists_when_access_token_expired(store, connection_id, monkeypatch):
    cs.save_hosted_tiktok_token(store, connection_id, _expired_token())
    refreshed = _valid_token()
    refreshed["access_token"] = "refreshed_access_token"
    monkeypatch.setattr(tiktok_auth, "refresh_access_token", lambda refresh_token: refreshed)

    result = cs.get_hosted_tiktok_access_token(store, connection_id)

    assert result == "refreshed_access_token"
    assert cs.load_hosted_tiktok_token(store, connection_id)["access_token"] == "refreshed_access_token"


def test_raises_reauthorization_required_when_refresh_token_itself_expired(store, connection_id):
    now = datetime.now(timezone.utc)
    token = _valid_token(now, access_ttl_minutes=-10, refresh_ttl_days=-1)
    cs.save_hosted_tiktok_token(store, connection_id, token)

    with pytest.raises(tiktok_auth.TikTokReauthorizationRequiredError):
        cs.get_hosted_tiktok_access_token(store, connection_id)


def test_propagates_reauthorization_required_when_tiktok_rejects_the_refresh(store, connection_id, monkeypatch):
    cs.save_hosted_tiktok_token(store, connection_id, _expired_token())

    def _reject(refresh_token):
        raise tiktok_auth.TikTokReauthorizationRequiredError("TikTok rejected the refresh.")

    monkeypatch.setattr(tiktok_auth, "refresh_access_token", _reject)

    with pytest.raises(tiktok_auth.TikTokReauthorizationRequiredError):
        cs.get_hosted_tiktok_access_token(store, connection_id)


def test_lost_cas_race_retries_and_returns_the_winners_token(store, connection_id, monkeypatch):
    """Simulates two concurrent requests both observing an expired token:
    this call's CAS write loses (another request already persisted a
    refreshed token first, by mutating the row out from under this call
    between its read and its write), so it must re-read and return the
    winner's already-persisted token instead of erroring or double-writing."""
    cs.save_hosted_tiktok_token(store, connection_id, _expired_token())

    winner_token = _valid_token()
    winner_token["access_token"] = "winner_access_token"
    real_refresh = tiktok_auth.refresh_access_token
    call_count = {"n": 0}

    def _refresh_that_loses_the_race(refresh_token):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate a concurrent request winning first: persist a
            # different refreshed token via a completely separate,
            # unconditional write before this call's own CAS attempt runs.
            cs.save_hosted_tiktok_token(store, connection_id, winner_token)
        return _valid_token()  # this call's own (losing) refresh result

    monkeypatch.setattr(tiktok_auth, "refresh_access_token", _refresh_that_loses_the_race)

    result = cs.get_hosted_tiktok_access_token(store, connection_id)

    assert result == "winner_access_token"
