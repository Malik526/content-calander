"""
postgres_migrate.py — Versioned, deterministic Postgres schema migrations
(Milestone 3.3).

What it does:
  A small, dependency-free migration mechanism: plain numbered .sql files
  under persistence/postgres_migrations/, tracked in a schema_migrations
  table (one row per applied filename), applied in filename order, each
  inside its own transaction. Deliberately not Alembic — Alembic pulls in
  SQLAlchemy as a real dependency even for raw-SQL migration bodies, and
  this codebase's own established preference (see AGENTS.md, and Milestone
  3.3's evaluation record "Migration Framework") is hand-written SQL over an
  ORM unless a real need is demonstrated; none was here. See
  docs/decisions/0008-postgres-persistence-migration.md.

  Idempotent: re-running apply_migrations() against a database that already
  has every migration applied is a no-op (each filename is only ever applied
  once, tracked by schema_migrations.version).

Dependencies:
  psycopg. config.POSTGRES_SCHEMA/POSTGRES_TEST_SCHEMA (via callers, not
  this module — see PostgresContentStore).
"""

from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

MIGRATIONS_DIR = Path(__file__).with_name("postgres_migrations")


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def _applied_versions(conn: psycopg.Connection) -> set[str]:
    """Uses an explicit tuple_row cursor rather than relying on the
    connection's own default row_factory (postgres_content_store.py always
    connects with row_factory=dict_row, under which plain integer indexing
    like row[0] would raise KeyError — a dict has no key 0) — this keeps
    the module correct regardless of what row_factory the caller's
    connection was opened with."""
    with conn.cursor(row_factory=tuple_row) as cur:
        rows = cur.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def pending_migrations(conn: psycopg.Connection) -> list[Path]:
    """Migration files (sorted by filename) not yet recorded as applied."""
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    all_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [path for path in all_files if path.name not in applied]


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply every not-yet-applied migration file, in filename order, each
    in its own transaction (schema DDL + the schema_migrations INSERT
    commit together, or neither does). Returns the list of filenames
    applied this call — empty if the database was already current."""
    applied_this_call = []
    for path in pending_migrations(conn):
        sql = path.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        applied_this_call.append(path.name)
    return applied_this_call
