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

  Concurrency-safe (2026-09-30 production fix): PostgresContentStore runs
  apply_migrations() on every construction, and the API constructs one per
  request, so a deploy that adds a migration has many processes/requests
  applying it at once. `CREATE TABLE IF NOT EXISTS` is not race-safe in
  Postgres — two transactions both see the table missing, both try to
  create it, and the loser fails on the catalog's unique index
  (`duplicate key value violates unique constraint
  "pg_class_relname_nsp_index"`), which is exactly how migration 0008
  surfaced in production. Each migration (and the schema_migrations
  bootstrap) now runs under a transaction-scoped advisory lock keyed to
  the current schema, and re-checks schema_migrations after acquiring it:
  concurrent appliers queue behind the first, then see the version
  already recorded and skip it. Transaction-scoped (pg_advisory_xact_lock),
  not session-scoped, so it releases on commit/rollback and stays correct
  behind a transaction-pooling connection pooler (Supabase's pooler).

Dependencies:
  psycopg. config.POSTGRES_SCHEMA/POSTGRES_TEST_SCHEMA (via callers, not
  this module — see PostgresContentStore).
"""

from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

MIGRATIONS_DIR = Path(__file__).with_name("postgres_migrations")


# One lock per schema (the test schema and "public" share a database but
# must not block each other). hashtext() is Postgres's own stable string hash.
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('content_automation.schema_migrations:' || current_schema()))"


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(_LOCK_SQL)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


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
    applied this call — empty if the database was already current.

    Safe to call concurrently from many processes (see module docstring):
    each migration's transaction first takes the schema's advisory lock,
    then re-checks schema_migrations, so a migration another caller applied
    while this one waited is skipped rather than re-run."""
    applied_this_call = []
    for path in pending_migrations(conn):
        sql = path.read_text(encoding="utf-8")
        with conn.transaction():
            conn.execute(_LOCK_SQL)
            with conn.cursor(row_factory=tuple_row) as cur:
                already_applied = cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s", (path.name,)
                ).fetchone()
            if already_applied:
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        applied_this_call.append(path.name)
    return applied_this_call
