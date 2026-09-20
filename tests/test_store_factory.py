"""Tests for persistence.store_factory.build_content_store: SQLite vs.
Postgres selection (Milestone 3.3, Phase 24 — production must never
silently fall back from a broken Postgres connection to local SQLite)."""

import psycopg
import pytest

from content_automation.persistence import store_factory
from content_automation.persistence.content_store import ContentStore


def test_empty_database_url_selects_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(store_factory, "DATABASE_URL", "")
    store = store_factory.build_content_store(db_path=tmp_path / "test.db")
    try:
        assert isinstance(store, ContentStore)
    finally:
        store.close()


def test_set_database_url_selects_postgres_and_never_falls_back_to_sqlite(monkeypatch):
    """A bogus (unreachable) DSN is deliberately used — proves Postgres is
    genuinely selected (not silently swapped for SQLite) and that a broken
    connection raises rather than quietly falling back to a local file."""
    monkeypatch.setattr(store_factory, "DATABASE_URL", "postgresql://user:pass@127.0.0.1:1/db")
    with pytest.raises(psycopg.OperationalError):
        store_factory.build_content_store()
