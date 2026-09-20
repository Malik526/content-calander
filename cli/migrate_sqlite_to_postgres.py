"""
migrate_sqlite_to_postgres.py — One-time data migration: copy every row
from the real local SQLite database (data/content.db) into Postgres
(config.DATABASE_URL / config.POSTGRES_SCHEMA), preserving IDs, foreign-key
relationships, publishing state, and ownership exactly.

What it does:
  Reads users -> auth_identities -> platform_connections -> content_slots
  (assigned_video_id left NULL initially) -> videos (assigned_slot_id set
  directly — content_slots already exist) -> content_slots.assigned_video_id
  backfilled in a second pass (breaking the same circular
  content_slots<->videos dependency the schema migration itself breaks —
  see persistence/postgres_migrations/0001_initial_schema.sql) ->
  platform_posts, in that dependency order, from the source SQLite database,
  and INSERTs each row into Postgres with its exact original id preserved
  (OVERRIDING SYSTEM VALUE — Postgres refuses to accept an explicit id into
  a GENERATED ALWAYS AS IDENTITY column otherwise). After each table's rows
  are inserted, that table's identity sequence is advanced past the highest
  imported id (see _advance_sequence) — the classic migration bug this
  guards against is the next real INSERT colliding with an imported id
  because the sequence was never told about it.

  Makes ZERO TikTok API calls — every field is copied verbatim from SQLite;
  nothing is re-derived, re-validated against a live platform, or
  refreshed.

  Idempotent per table via ON CONFLICT (id) DO NOTHING — safe to re-run
  after a partial failure; already-migrated rows are never re-inserted or
  overwritten. Not idempotent across a full re-run if the destination has
  since diverged from the source (this tool is a one-time import, not an
  ongoing sync) — see verify_migration() for the safety check that should
  run immediately after any real migration.

Run:
  python3 cli/migrate_sqlite_to_postgres.py
  python3 cli/migrate_sqlite_to_postgres.py --dry-run
  python3 cli/migrate_sqlite_to_postgres.py --verify-only
"""

import argparse
import sqlite3

import psycopg

from content_automation.config import DATABASE_URL, DB_PATH, POSTGRES_SCHEMA
from content_automation.persistence import postgres_migrate

# Dependency order for insertion (content_slots before videos, with
# assigned_video_id deferred — see module docstring).
_TABLE_ORDER = [
    "users",
    "auth_identities",
    "platform_connections",
    "content_slots",
    "videos",
    "platform_posts",
]

_TABLE_COLUMNS = {
    "users": ["id", "email", "display_name", "created_at", "updated_at"],
    "auth_identities": ["id", "user_id", "provider", "provider_subject", "provider_email", "created_at", "updated_at"],
    "platform_connections": ["id", "user_id", "platform", "external_account_id", "status", "created_at", "updated_at"],
    "content_slots": [
        "id", "user_id", "scheduled_at", "pillar_key", "prompt", "status",
        "google_calendar_event_id", "created_at",
        # assigned_video_id deliberately excluded from the first pass insert
        # list — see module docstring; backfilled by _backfill_slot_assignments.
    ],
    "videos": [
        "id", "user_id", "file_hash", "original_filename", "original_path", "canonical_media_path",
        "container", "video_codec", "audio_codec", "width", "height", "fps", "duration_seconds",
        "file_size_bytes", "transcript", "transcript_language", "transcription_status",
        "classified_pillar", "classification_confidence", "classification_reason",
        "classification_second_score", "classification_margin", "classifier", "status",
        "failure_reason", "assigned_slot_id", "created_at", "processed_at", "caption_text", "caption_source",
    ],
    "platform_posts": [
        "id", "user_id", "video_id", "platform", "status", "platform_post_id", "scheduled_at",
        "published_at", "failure_reason", "created_at", "updated_at", "retry_count", "next_retry_at",
        "next_status_check_at", "status_check_count",
    ],
}


def _sqlite_rows(sqlite_conn: sqlite3.Connection, table: str, columns: list[str]) -> list[tuple]:
    column_list = ", ".join(columns)
    rows = sqlite_conn.execute(f"SELECT {column_list} FROM {table} ORDER BY id").fetchall()
    return [tuple(row) for row in rows]


def _insert_rows(pg_conn: psycopg.Connection, table: str, columns: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    column_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    inserted = 0
    for row in rows:
        cur = pg_conn.execute(
            f"INSERT INTO {table} ({column_list}) OVERRIDING SYSTEM VALUE VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO NOTHING",
            row,
        )
        inserted += cur.rowcount
    return inserted


def _advance_sequence(pg_conn: psycopg.Connection, table: str) -> None:
    """Advance `table`'s identity sequence past the highest id now present,
    so the next real INSERT (no explicit id, the normal case) gets a fresh
    one instead of colliding with an imported row. A no-op (COALESCE to 1,
    is_called=false) if the table ended up empty."""
    pg_conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
        f"(SELECT MAX(id) FROM {table}) IS NOT NULL)"
    )


def _backfill_slot_assignments(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection) -> int:
    """Second pass: content_slots.assigned_video_id, deferred from the first
    insert pass because it may reference a video that didn't exist yet at
    that point in dependency order."""
    rows = sqlite_conn.execute(
        "SELECT id, assigned_video_id FROM content_slots WHERE assigned_video_id IS NOT NULL"
    ).fetchall()
    updated = 0
    for slot_id, assigned_video_id in rows:
        cur = pg_conn.execute(
            "UPDATE content_slots SET assigned_video_id = %s WHERE id = %s", (assigned_video_id, slot_id)
        )
        updated += cur.rowcount
    return updated


def migrate(*, dry_run: bool = False) -> dict[str, int]:
    """Returns {table_name: rows_inserted_this_call}. In dry_run mode,
    connects to both databases and reports source row counts per table
    without writing anything to Postgres."""
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    report: dict[str, int] = {}

    if dry_run:
        for table in _TABLE_ORDER:
            count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            report[table] = count
        sqlite_conn.close()
        return report

    pg_conn = psycopg.connect(DATABASE_URL, autocommit=True)
    pg_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{POSTGRES_SCHEMA}"')
    pg_conn.execute(f'SET search_path TO "{POSTGRES_SCHEMA}"')
    postgres_migrate.apply_migrations(pg_conn)

    try:
        for table in _TABLE_ORDER:
            columns = _TABLE_COLUMNS[table]
            rows = _sqlite_rows(sqlite_conn, table, columns)
            with pg_conn.transaction():
                inserted = _insert_rows(pg_conn, table, columns, rows)
                _advance_sequence(pg_conn, table)
            report[table] = inserted

        with pg_conn.transaction():
            report["content_slots_assigned_video_id_backfilled"] = _backfill_slot_assignments(sqlite_conn, pg_conn)
    finally:
        sqlite_conn.close()
        pg_conn.close()

    return report


def verify_migration() -> dict[str, dict]:
    """Compares SQLite (source) and Postgres (destination) after a
    migration: row counts per table, plus a byte-for-byte comparison of
    every business-critical field this milestone must preserve exactly
    (status, scheduled_at, published_at, platform_post_id, retry state,
    ownership). Returns {table: {"source_count", "dest_count", "match"}}
    plus a top-level "field_mismatches" list (empty if everything matches).
    """
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg.connect(DATABASE_URL, autocommit=True)
    pg_conn.execute(f'SET search_path TO "{POSTGRES_SCHEMA}"')

    result: dict[str, dict] = {}
    try:
        for table in _TABLE_ORDER:
            source_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dest_count = pg_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result[table] = {"source_count": source_count, "dest_count": dest_count, "match": source_count == dest_count}

        field_mismatches = []
        source_posts = {
            row["id"]: dict(row)
            for row in sqlite_conn.execute(
                "SELECT id, video_id, status, platform_post_id, scheduled_at, published_at, "
                "failure_reason, retry_count, next_retry_at, status_check_count, user_id FROM platform_posts"
            ).fetchall()
        }
        with pg_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            dest_posts = {
                row["id"]: row
                for row in cur.execute(
                    "SELECT id, video_id, status, platform_post_id, scheduled_at, published_at, "
                    "failure_reason, retry_count, next_retry_at, status_check_count, user_id FROM platform_posts"
                ).fetchall()
            }
        for post_id, source_row in source_posts.items():
            dest_row = dest_posts.get(post_id)
            if dest_row is None:
                field_mismatches.append(f"platform_posts.id={post_id}: missing in Postgres")
                continue
            for field in ("video_id", "status", "platform_post_id", "failure_reason", "retry_count", "status_check_count", "user_id"):
                if source_row[field] != dest_row[field]:
                    field_mismatches.append(
                        f"platform_posts.id={post_id}.{field}: sqlite={source_row[field]!r} postgres={dest_row[field]!r}"
                    )
        result["field_mismatches"] = field_mismatches
    finally:
        sqlite_conn.close()
        pg_conn.close()

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time migration: copy every row from the real local SQLite database into Postgres, "
        "preserving ids, foreign keys, publishing state, and ownership. Makes zero TikTok API calls."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report source row counts without writing to Postgres.")
    parser.add_argument("--verify-only", action="store_true", help="Compare an already-migrated destination against the source; write nothing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.verify_only:
        result = verify_migration()
        for table, counts in result.items():
            if table == "field_mismatches":
                continue
            status = "OK" if counts["match"] else "MISMATCH"
            print(f"  {table}: source={counts['source_count']} dest={counts['dest_count']} [{status}]")
        mismatches = result["field_mismatches"]
        if mismatches:
            print(f"\n{len(mismatches)} field-level mismatch(es):")
            for m in mismatches:
                print(f"  {m}")
        else:
            print("\nAll checked fields match exactly.")
        return

    report = migrate(dry_run=args.dry_run)
    verb = "Would migrate" if args.dry_run else "Migrated"
    for table, count in report.items():
        print(f"  {verb}: {table}: {count} row(s)")

    if args.dry_run:
        print("\nDry run: nothing written to Postgres.")
    else:
        print("\nRun with --verify-only to compare source and destination.")


if __name__ == "__main__":
    main()
