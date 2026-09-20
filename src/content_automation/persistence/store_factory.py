"""
store_factory.py — build_content_store(): the one place runtime decides
SQLite vs. Postgres (Milestone 3.3).

What it does:
  DATABASE_URL set (config.py, from .env) -> PostgresContentStore.
  DATABASE_URL unset -> ContentStore (SQLite), unchanged default.

  If DATABASE_URL is set but the Postgres connection fails, this raises —
  it never silently falls back to local SQLite. A production deployment
  with a broken Postgres connection string must fail loudly, not quietly
  start writing to a local file nobody is looking at (see
  docs/decisions/0008-postgres-persistence-migration.md "Runtime
  Configuration").

  Not yet used by any CLI entry point — every cli/*.py still constructs
  ContentStore() directly, matching Milestone 3.3's own scope (prove the
  Postgres backend works and is tested, not cut the SQLite deployment over
  by default). A future milestone that wants a CLI/service to actually run
  against Postgres calls this instead of ContentStore() directly.

Dependencies:
  content_automation.config (DATABASE_URL), persistence.content_store,
  persistence.postgres_content_store.
"""

from content_automation.config import DATABASE_URL
from content_automation.persistence.content_store import ContentStore
from content_automation.persistence.protocol import ContentStoreProtocol


def build_content_store(**kwargs) -> ContentStoreProtocol:
    """Return a real, open ContentStore (SQLite) or PostgresContentStore,
    selected by whether config.DATABASE_URL is set. **kwargs are forwarded
    to whichever constructor is chosen (e.g. db_path= for SQLite, dsn=/
    schema= for Postgres) — callers that need backend-specific overrides
    still can, but the normal call is build_content_store() with no
    arguments."""
    if DATABASE_URL:
        from content_automation.persistence.postgres_content_store import PostgresContentStore

        # dsn is passed explicitly (this module's own DATABASE_URL, not
        # PostgresContentStore's default parameter value) so that anything
        # which reconfigures *this* module's DATABASE_URL — a test
        # monkeypatch, or a future runtime override — is actually honored
        # here, rather than silently using whatever DATABASE_URL
        # postgres_content_store.py happened to import at its own load time.
        kwargs.setdefault("dsn", DATABASE_URL)
        return PostgresContentStore(**kwargs)
    return ContentStore(**kwargs)
