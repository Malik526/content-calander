"""Tests for publishing.instagram.credential_store (Milestone 4.1): encrypted
long-lived Instagram credentials in the shared platform_credentials table,
and refresh timing, locking and compare-and-swap. Meta is never called —
oauth.refresh_long_lived_token is replaced per test."""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from content_automation import config
from content_automation.persistence.content_store import ContentStore, CredentialRefreshLockTimeout
from content_automation.publishing import credential_encryption
from content_automation.publishing.instagram import credential_store as cs
from content_automation.publishing.instagram import oauth as ig
from content_automation.publishing.tiktok import credential_store as tiktok_cs

DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(credential_encryption, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))


@pytest.fixture(autouse=True)
def refresh_window(monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS", 30 * 86_400)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def connection_id(store):
    user = store.create_user("a@example.com", "A", "2026-10-01T00:00:00+00:00")
    return store.get_or_create_platform_connection(user.id, "instagram", external_account_id="17841400000000001").id


def _token(*, expires_in=55 * DAY, age=5 * DAY, refreshed_age=None, access="long-lived-token"):
    now = datetime.now(timezone.utc)
    return {
        "access_token": access, "token_type": "bearer", "expires_at": (now + expires_in).isoformat(),
        "user_id": "17841400000000001", "permissions": ["instagram_business_basic"],
        "obtained_at": (now - age).isoformat(),
        "last_refreshed_at": (now - refreshed_age).isoformat() if refreshed_age is not None else None,
    }


def _refreshed(stored, access="refreshed-token"):
    now = datetime.now(timezone.utc)
    return {**stored, "access_token": access, "expires_at": (now + 60 * DAY).isoformat(), "last_refreshed_at": now.isoformat()}


def _never_refresh(*_a, **_k):
    pytest.fail("must not call Instagram's refresh endpoint")


# --- storage ----------------------------------------------------------------------

def test_save_then_load_roundtrips_and_is_never_plaintext(store, connection_id):
    token = _token()
    cs.save_hosted_instagram_token(store, connection_id, token)
    assert cs.load_hosted_instagram_token(store, connection_id) == token
    payload = store.get_platform_credential(connection_id).encrypted_payload
    assert "long-lived-token" not in payload and "17841400000000001" not in payload


def test_load_returns_none_when_nothing_saved(store, connection_id):
    assert cs.load_hosted_instagram_token(store, connection_id) is None


def test_instagram_and_tiktok_share_one_encryption_scheme(store, connection_id):
    cs.save_hosted_instagram_token(store, connection_id, _token())
    payload = store.get_platform_credential(connection_id).encrypted_payload
    assert tiktok_cs.decrypt_token(payload)["access_token"] == "long-lived-token"


# --- refresh_due ------------------------------------------------------------------

@pytest.mark.parametrize(
    ("token_kwargs", "due"),
    [
        ({"expires_in": 55 * DAY, "age": 5 * DAY}, False),            # outside the 30-day window
        ({"expires_in": 20 * DAY, "age": 40 * DAY}, True),            # inside the window, old enough
        ({"expires_in": 20 * DAY, "age": 40 * DAY, "refreshed_age": timedelta(hours=23)}, False),  # refreshed < 24 h ago
        ({"expires_in": 20 * DAY, "age": 40 * DAY, "refreshed_age": timedelta(hours=25)}, True),
        ({"expires_in": -timedelta(minutes=1), "age": 61 * DAY}, False),  # expired: can't be refreshed
    ],
)
def test_refresh_is_due_only_when_instagram_permits_it_and_inside_the_window(token_kwargs, due):
    assert cs.refresh_due(_token(**token_kwargs), datetime.now(timezone.utc)) is due


# --- get_hosted_instagram_access_token --------------------------------------------

def test_a_token_outside_the_refresh_window_is_returned_without_calling_instagram(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token())
    monkeypatch.setattr(ig, "refresh_long_lived_token", _never_refresh)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "long-lived-token"


def test_a_young_token_is_never_refreshed_even_inside_a_wide_window(store, connection_id, monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS", 59 * 86_400)
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=58 * DAY, age=timedelta(hours=2)))
    monkeypatch.setattr(ig, "refresh_long_lived_token", _never_refresh)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "long-lived-token"


def test_a_due_token_is_refreshed_persisted_and_returned(store, connection_id, monkeypatch):
    stored = _token(expires_in=10 * DAY, age=50 * DAY)
    cs.save_hosted_instagram_token(store, connection_id, stored)
    monkeypatch.setattr(ig, "refresh_long_lived_token", lambda token: _refreshed(token))

    assert cs.get_hosted_instagram_access_token(store, connection_id) == "refreshed-token"

    saved = cs.load_hosted_instagram_token(store, connection_id)
    assert saved["access_token"] == "refreshed-token" and saved["last_refreshed_at"] is not None
    assert saved["obtained_at"] == stored["obtained_at"] and saved["user_id"] == stored["user_id"]
    # Just refreshed: the next call doesn't refresh again.
    monkeypatch.setattr(ig, "refresh_long_lived_token", _never_refresh)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "refreshed-token"


def test_refresh_runs_inside_the_per_connection_lock(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))
    held = []

    @contextmanager
    def recording_lock(platform_connection_id):
        held.append(platform_connection_id)
        yield

    def refresh(token):
        assert held == [connection_id], "refresh must run while the connection's lock is held"
        return _refreshed(token)

    monkeypatch.setattr(store, "credential_refresh_lock", recording_lock)
    monkeypatch.setattr(ig, "refresh_long_lived_token", refresh)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "refreshed-token"


def test_a_token_refreshed_by_the_previous_lock_holder_is_reused(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))

    @contextmanager
    def lock_after_another_process_refreshed(platform_connection_id):
        # While we waited, the holder before us refreshed and committed.
        stored = cs.load_hosted_instagram_token(store, platform_connection_id)
        store.update_platform_credential_if_unchanged(
            platform_connection_id, credential_encryption.encrypt_credential(_refreshed(stored, "their-token")),
            store.get_platform_credential(platform_connection_id).updated_at, datetime.now(timezone.utc).isoformat(),
        )
        yield

    monkeypatch.setattr(store, "credential_refresh_lock", lock_after_another_process_refreshed)
    monkeypatch.setattr(ig, "refresh_long_lived_token", _never_refresh)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "their-token"


def test_a_lost_compare_and_swap_rereads_instead_of_overwriting(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))

    def refresh_while_user_reconnects(token):
        # A reconnect lands between our read and our write.
        time.sleep(0.001)  # distinct updated_at
        cs.save_hosted_instagram_token(store, connection_id, _token(access="reconnected-token", age=timedelta(0), expires_in=60 * DAY))
        return _refreshed(token, "our-stale-refresh")

    monkeypatch.setattr(ig, "refresh_long_lived_token", refresh_while_user_reconnects)
    assert cs.get_hosted_instagram_access_token(store, connection_id) == "reconnected-token"
    assert cs.load_hosted_instagram_token(store, connection_id)["access_token"] == "reconnected-token"


def test_missing_credential_requires_reauthorization(store, connection_id):
    with pytest.raises(ig.InstagramReauthorizationRequiredError):
        cs.get_hosted_instagram_access_token(store, connection_id)


def test_expired_token_requires_reauthorization_without_calling_instagram(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=-timedelta(minutes=1), age=61 * DAY))
    monkeypatch.setattr(ig, "refresh_long_lived_token", _never_refresh)
    with pytest.raises(ig.InstagramReauthorizationRequiredError):
        cs.get_hosted_instagram_access_token(store, connection_id)


def test_a_rejected_refresh_requires_reauthorization(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))

    def rejected(_token):
        raise ig.InstagramReauthorizationRequiredError("rejected", http_status=400)

    monkeypatch.setattr(ig, "refresh_long_lived_token", rejected)
    with pytest.raises(ig.InstagramReauthorizationRequiredError):
        cs.get_hosted_instagram_access_token(store, connection_id)


def test_a_transient_refresh_failure_keeps_using_a_still_valid_token(store, connection_id, monkeypatch, caplog):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))

    def unreachable(_token):
        raise ig.InstagramAuthError("Could not reach Instagram", reason_code="NETWORK_ERROR")

    monkeypatch.setattr(ig, "refresh_long_lived_token", unreachable)
    with caplog.at_level("INFO"):
        assert cs.get_hosted_instagram_access_token(store, connection_id) == "long-lived-token"
    assert "credential_refresh_deferred" in caplog.text and "long-lived-token" not in caplog.text


def test_a_transient_refresh_failure_right_before_expiry_is_raised(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=timedelta(minutes=2), age=60 * DAY))

    def unreachable(_token):
        raise ig.InstagramAuthError("Could not reach Instagram", reason_code="NETWORK_ERROR")

    monkeypatch.setattr(ig, "refresh_long_lived_token", unreachable)
    with pytest.raises(ig.InstagramAuthError) as error:
        cs.get_hosted_instagram_access_token(store, connection_id)
    assert error.value.reason_code == "NETWORK_ERROR"


def test_a_lock_timeout_is_a_retryable_busy_error(store, connection_id, monkeypatch):
    cs.save_hosted_instagram_token(store, connection_id, _token(expires_in=10 * DAY, age=50 * DAY))

    @contextmanager
    def busy(_platform_connection_id):
        raise CredentialRefreshLockTimeout("not acquired")
        yield  # pragma: no cover

    monkeypatch.setattr(store, "credential_refresh_lock", busy)
    with pytest.raises(ig.InstagramAuthError) as error:
        cs.get_hosted_instagram_access_token(store, connection_id)
    assert error.value.reason_code == "CREDENTIAL_REFRESH_BUSY"


# --- real concurrency ---------------------------------------------------------------

@dataclass
class _Record:
    encrypted_payload: str
    updated_at: str


class _ThreadSafeStore:
    """The three ContentStoreProtocol methods the refresh path uses, with a
    real per-connection lock — what PostgresContentStore's advisory lock
    provides across processes — so callers on separate threads genuinely
    race. (SQLite connections can't be shared across threads.)"""

    def __init__(self, token):
        self._record = _Record(credential_encryption.encrypt_credential(token), "v0")
        self._lock = threading.Lock()
        self._mutex = threading.Lock()
        self._version = 0

    def get_platform_credential(self, _platform_connection_id):
        with self._mutex:
            return self._record

    def update_platform_credential_if_unchanged(self, _cid, payload, expected_updated_at, _new_updated_at):
        with self._mutex:
            if self._record.updated_at != expected_updated_at:
                return False
            self._version += 1
            self._record = _Record(payload, f"v{self._version}")
            return True

    @contextmanager
    def credential_refresh_lock(self, _platform_connection_id):
        with self._lock:
            yield


def test_concurrent_callers_refresh_exactly_once_and_all_get_the_new_token(monkeypatch):
    store = _ThreadSafeStore(_token(expires_in=10 * DAY, age=50 * DAY))
    calls = []

    def slow_refresh(token):
        calls.append(1)
        time.sleep(0.05)  # every other caller is now waiting on the lock
        return _refreshed(token)

    monkeypatch.setattr(ig, "refresh_long_lived_token", slow_refresh)
    results, errors = [], []

    def worker():
        try:
            results.append(cs.get_hosted_instagram_access_token(store, 1))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(calls) == 1
    assert results == ["refreshed-token"] * 8
