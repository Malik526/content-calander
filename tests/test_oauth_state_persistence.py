"""Tests for the Milestone 4.1 oauth_states / platform_connections changes
in the SQLite ContentStore: the nullable return_target column on fresh and
pre-4.1 databases, atomic platform-scoped state consumption, and updating
a connection's external account id. The Postgres equivalents are in
test_postgres_content_store.py (skipped without DATABASE_URL)."""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from content_automation.persistence.content_store import ContentStore

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _iso(delta=timedelta(0)):
    return (NOW + delta).isoformat()


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def user(store):
    return store.create_user("a@example.com", "A", _iso())


def _state(store, user, platform, state, *, return_target=None, ttl=timedelta(minutes=10)):
    return store.create_oauth_state(user.id, platform, state, "", "https://api.example.com/cb", _iso(), _iso(ttl), return_target=return_target)


def test_fresh_database_has_a_nullable_return_target(store):
    columns = {row["name"]: row for row in store._conn.execute("PRAGMA table_info(oauth_states)")}
    assert "return_target" in columns and columns["return_target"]["notnull"] == 0


def test_return_target_roundtrips_and_defaults_to_none(store, user):
    with_target = _state(store, user, "instagram", "s1", return_target="https://app.example.com/app/settings")
    legacy_call = store.create_oauth_state(user.id, "tiktok", "s2", "verifier", "https://api.example.com/cb", _iso(), _iso(timedelta(minutes=10)))
    assert with_target.return_target == "https://app.example.com/app/settings"
    assert legacy_call.return_target is None
    assert store.consume_oauth_state("s1", _iso(), platform="instagram").return_target == "https://app.example.com/app/settings"
    assert store.consume_oauth_state("s2", _iso(), platform="tiktok").return_target is None


def test_a_pre_4_1_database_gains_the_column_and_keeps_its_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    with ContentStore(db_path=db_path) as store:
        user = store.create_user("a@example.com", "A", _iso())
    # Rebuild oauth_states exactly as Milestone 3.6 created it, with a live row.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        DROP TABLE oauth_states;
        CREATE TABLE oauth_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            platform TEXT NOT NULL,
            state TEXT NOT NULL UNIQUE,
            code_verifier TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO oauth_states (user_id, platform, state, code_verifier, redirect_uri, created_at, expires_at) "
        "VALUES (?, 'tiktok', 'legacy-state', 'v', 'https://api.example.com/cb', ?, ?)",
        (user.id, _iso(), _iso(timedelta(minutes=10))),
    )
    conn.commit()
    conn.close()

    with ContentStore(db_path=db_path) as store:
        consumed = store.consume_oauth_state("legacy-state", _iso(), platform="tiktok")
        assert consumed is not None and consumed.return_target is None
        _state(store, user, "instagram", "new-state", return_target="https://app.example.com/app/settings")
        assert store.consume_oauth_state("new-state", _iso(), platform="instagram").return_target.endswith("/app/settings")
    with ContentStore(db_path=db_path):  # reopening an already-migrated database is a no-op
        pass


def test_consume_is_single_use(store, user):
    _state(store, user, "instagram", "s")
    assert store.consume_oauth_state("s", _iso(), platform="instagram") is not None
    assert store.consume_oauth_state("s", _iso(timedelta(seconds=1)), platform="instagram") is None


def test_consume_rejects_expired_and_unknown_states(store, user):
    _state(store, user, "instagram", "s", ttl=timedelta(minutes=10))
    assert store.consume_oauth_state("s", _iso(timedelta(minutes=10)), platform="instagram") is None
    assert store.consume_oauth_state("never-issued", _iso(), platform="instagram") is None
    row = store._conn.execute("SELECT consumed_at FROM oauth_states WHERE state = 's'").fetchone()
    assert row["consumed_at"] is None  # an expired attempt is rejected, not marked


def test_consume_is_scoped_to_the_platform_and_never_marks_another_platforms_state(store, user):
    _state(store, user, "tiktok", "tiktok-state")
    _state(store, user, "instagram", "instagram-state")

    assert store.consume_oauth_state("tiktok-state", _iso(), platform="instagram") is None
    assert store.consume_oauth_state("instagram-state", _iso(), platform="tiktok") is None

    assert store.consume_oauth_state("tiktok-state", _iso(), platform="tiktok").platform == "tiktok"
    consumed = store.consume_oauth_state("instagram-state", _iso(), platform="instagram")
    assert (consumed.platform, consumed.user_id, consumed.code_verifier, consumed.consumed_at) == ("instagram", user.id, "", _iso())


def test_concurrent_consumers_of_one_state_have_exactly_one_winner(tmp_path):
    db_path = tmp_path / "race.db"
    with ContentStore(db_path=db_path) as store:
        user = store.create_user("a@example.com", "A", _iso())
        _state(store, user, "instagram", "contested")

    winners, barrier = [], threading.Barrier(6)

    def consume():
        with ContentStore(db_path=db_path) as own_store:
            barrier.wait()
            for _ in range(20):  # SQLite may report "database is locked" under contention
                try:
                    winners.append(own_store.consume_oauth_state("contested", _iso(), platform="instagram"))
                    return
                except sqlite3.OperationalError:
                    threading.Event().wait(0.01)

    threads = [threading.Thread(target=consume) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 6
    assert len([w for w in winners if w is not None]) == 1


def test_update_external_account_changes_only_that_connection(store, user):
    other = store.create_user("b@example.com", "B", _iso())
    mine = store.get_or_create_platform_connection(user.id, "instagram", external_account_id="111")
    theirs = store.get_or_create_platform_connection(other.id, "instagram", external_account_id="222")

    store.update_platform_connection_external_account(mine.id, "333", _iso(timedelta(minutes=1)))

    updated = store.get_platform_connection(user.id, "instagram")
    assert (updated.external_account_id, updated.updated_at) == ("333", _iso(timedelta(minutes=1)))
    assert store.get_platform_connection(other.id, "instagram").external_account_id == theirs.external_account_id
