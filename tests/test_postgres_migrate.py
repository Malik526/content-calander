"""Regression tests for persistence.postgres_migrate under concurrency
(2026-09-30 production fix). Production saw migration 0008 fail with
`duplicate key value violates unique constraint "pg_class_relname_nsp_index"
... (video_hashtags_id_seq, ...)` because every API request constructs a
PostgresContentStore, which applies pending migrations, and two requests
raced to run the same `CREATE TABLE IF NOT EXISTS` — which is not
concurrency-safe in Postgres.

Real Postgres only (skipped without DATABASE_URL), in a dedicated
throwaway schema — never config.POSTGRES_SCHEMA ("public")."""

import threading
import time

import psycopg
import pytest

from content_automation.config import DATABASE_URL, POSTGRES_SCHEMA, POSTGRES_TEST_SCHEMA
from content_automation.persistence import postgres_migrate
from content_automation.persistence.postgres_content_store import _connect

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not configured — Postgres integration tests skipped")

SCHEMA = f"{POSTGRES_TEST_SCHEMA}_migrate"
assert SCHEMA != POSTGRES_SCHEMA


@pytest.fixture
def fresh_schema():
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        admin.execute(f'CREATE SCHEMA "{SCHEMA}"')
    yield SCHEMA
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


def _run_concurrently(count, target):
    """Run target() in `count` threads; return (results, errors)."""
    results, errors = [None] * count, []

    def runner(index):
        try:
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001 — the test asserts on these
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


def _versions(schema):
    with _connect(DATABASE_URL, schema) as conn:
        return [row["version"] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def test_second_applier_waits_for_and_skips_an_in_flight_migration(fresh_schema, tmp_path, monkeypatch):
    """Deterministic reproduction of the production interleaving: applier A
    creates an identity table and holds its transaction open; applier B
    starts meanwhile. Before the fix B's CREATE TABLE IF NOT EXISTS blocked
    on the catalog and then failed with the pg_class unique violation once A
    committed. Now B waits on the advisory lock, sees A's version recorded,
    and skips it."""
    (tmp_path / "0001_race.sql").write_text(
        "CREATE TABLE IF NOT EXISTS race_target (\n"
        "    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY\n"
        ");\n"
        "SELECT pg_sleep(1.5);\n"
    )
    monkeypatch.setattr(postgres_migrate, "MIGRATIONS_DIR", tmp_path)
    # Bootstrap schema_migrations up front so both appliers race on the
    # migration itself, as in production (where the table long existed).
    with _connect(DATABASE_URL, fresh_schema) as conn:
        postgres_migrate.pending_migrations(conn)

    def apply(index):
        if index == 1:
            time.sleep(0.4)  # A is now inside its open transaction
        with _connect(DATABASE_URL, fresh_schema) as conn:
            return postgres_migrate.apply_migrations(conn)

    results, errors = _run_concurrently(2, apply)

    assert errors == []
    assert sorted(results, key=len) == [[], ["0001_race.sql"]]
    assert _versions(fresh_schema) == ["0001_race.sql"]


def test_many_concurrent_appliers_on_the_real_migrations(fresh_schema):
    """The production shape: several requests constructing stores at once
    against a database missing migrations."""
    barrier = threading.Barrier(6)

    def apply(_index):
        with _connect(DATABASE_URL, fresh_schema) as conn:
            barrier.wait()
            return postgres_migrate.apply_migrations(conn)

    results, errors = _run_concurrently(6, apply)

    assert errors == []
    every_file = sorted(path.name for path in postgres_migrate.MIGRATIONS_DIR.glob("*.sql"))
    applied = sorted(name for result in results for name in result)
    assert applied == every_file  # each migration applied exactly once, across all callers
    assert _versions(fresh_schema) == every_file


def test_0008_and_0009_reapply_safely_over_existing_objects_and_keep_rows(fresh_schema):
    """Recovery path: video_hashtags already exists (with rows) but 0008/0009
    are unrecorded. Re-applying must succeed, preserve the rows, and still
    apply 0009."""
    with _connect(DATABASE_URL, fresh_schema) as conn:
        postgres_migrate.apply_migrations(conn)
        user_id = conn.execute(
            "INSERT INTO users (email, display_name, created_at, updated_at) "
            "VALUES ('a@example.com', 'A', '2026-01-01', '2026-01-01') RETURNING id"
        ).fetchone()["id"]
        video_id = conn.execute(
            "INSERT INTO videos (file_hash, original_filename, original_path, status, created_at, user_id) "
            "VALUES ('h', 'v.mp4', '/v.mp4', 'DISCOVERED', '2026-01-01', %s) RETURNING id",
            (user_id,),
        ).fetchone()["id"]
        conn.execute("INSERT INTO video_hashtags (video_id, position, hashtag) VALUES (%s, 0, '#kept')", (video_id,))
        conn.execute("ALTER TABLE platform_posts DROP COLUMN failure_code")
        conn.execute(
            "DELETE FROM schema_migrations WHERE version IN "
            "('0008_add_video_hashtags.sql', '0009_add_platform_posts_failure_code.sql')"
        )

        applied = postgres_migrate.apply_migrations(conn)

        assert applied == ["0008_add_video_hashtags.sql", "0009_add_platform_posts_failure_code.sql"]
        assert [r["hashtag"] for r in conn.execute("SELECT hashtag FROM video_hashtags").fetchall()] == ["#kept"]
        assert conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
            "AND table_name = 'platform_posts' AND column_name = 'failure_code'",
            (fresh_schema,),
        ).fetchone()
        assert postgres_migrate.apply_migrations(conn) == []
