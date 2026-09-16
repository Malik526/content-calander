"""
content_store.py — SQLite persistence for videos, content_slots, and
platform_posts.

What it does:
  Owns the three tables that make the content-processing/publishing
  pipeline restart-safe and idempotent: `videos` (one row per ingested
  file, keyed by content hash), `content_slots` (one row per calendar
  posting slot, written by generate_calendar.py and consumed by
  process_content.py / slot_matcher.py), and `platform_posts` (one row per
  video-platform publishing attempt, written/read by publish_tiktok.py —
  see docs/decisions/0006-tiktok-publisher-foundation.md). Each owns a
  distinct concern: videos is canonical content/media metadata,
  content_slots is scheduling assignment, platform_posts is external
  publishing state/result — never cram one concern's state into another
  table's columns.

  Google Calendar remains the source of truth for what gets posted when;
  content_slots is an internal mirror that lets process_content.py query and
  atomically claim open slots without re-deriving the schedule.

  content_slots.scheduled_at is unique on its own (not (scheduled_at,
  pillar_key)) — one posting opportunity is one slot, regardless of which
  pillar the strategy later assigns it. See
  docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md.
  _migrate_content_slots_unique_constraint() upgrades any database created
  under the old two-column constraint the first time it's opened. It runs
  with PRAGMA legacy_alter_table=ON so renaming content_slots during the
  rebuild never rewrites videos.assigned_slot_id's REFERENCES clause to the
  temporary table name — SQLite's enhanced ALTER TABLE RENAME behavior does
  exactly that by default, which is what corrupted a real database's schema
  before this guard existed. _repair_videos_assigned_slot_fk() detects and
  fixes that already-corrupted state (assigned_slot_id referencing anything
  other than content_slots) on databases that migrated before the guard was
  added, by rebuilding videos the same safe way.

Dependencies:
  stdlib sqlite3 only.
"""

import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config import DB_PATH

SCHEMA_VIDEOS = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    canonical_media_path TEXT,
    container TEXT,
    video_codec TEXT,
    audio_codec TEXT,
    width INTEGER,
    height INTEGER,
    fps REAL,
    duration_seconds REAL,
    file_size_bytes INTEGER,
    transcript TEXT,
    transcript_language TEXT,
    transcription_status TEXT,
    classified_pillar TEXT,
    classification_confidence REAL,
    classification_reason TEXT,
    classification_second_score REAL,
    classification_margin REAL,
    classifier TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    failure_reason TEXT,
    assigned_slot_id INTEGER REFERENCES content_slots(id),
    created_at TEXT NOT NULL,
    processed_at TEXT
);
"""

SCHEMA_CONTENT_SLOTS = """
CREATE TABLE IF NOT EXISTS content_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_at TEXT NOT NULL UNIQUE,
    pillar_key TEXT,
    prompt TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    assigned_video_id INTEGER REFERENCES videos(id),
    google_calendar_event_id TEXT,
    created_at TEXT NOT NULL
);
"""

# Added Milestone 2.0 (TikTok Publisher Foundation — see
# docs/decisions/0006-tiktok-publisher-foundation.md). Deliberately its own
# table rather than columns on `videos`: `videos` is canonical content/media
# metadata, `content_slots` is scheduling assignment, `platform_posts` is
# external publishing state/result — one video can eventually have zero or
# more platform_posts rows (one per platform), so this could never be a
# 1:1 column addition to `videos` even for a single platform today.
# UNIQUE(video_id, platform) is the idempotency primitive: a video can have
# at most one publishing record per platform, so a repeated manual publish
# attempt must look that row up (get_platform_post) rather than ever being
# able to insert a second one.
SCHEMA_PLATFORM_POSTS = """
CREATE TABLE IF NOT EXISTS platform_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    platform_post_id TEXT,
    scheduled_at TEXT,
    published_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(video_id, platform)
);
"""

_SLOT_STATUS_PRIORITY = {"OPEN": 0, "FAILED": 0, "ASSIGNED": 1, "PUBLISHED": 2}

# New nullable videos columns added for Milestone 1.2 (local embedding
# classification observability — see
# docs/decisions/0003-local-embedding-classification.md) and Milestone 1.3
# (first-class caption state — see
# docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md).
# Adding a nullable column is a simple ALTER TABLE, unlike the content_slots
# rebuild below.
_VIDEOS_MIGRATION_COLUMNS = {
    "classification_second_score": "REAL",
    "classification_margin": "REAL",
    "classifier": "TEXT",
    "caption_text": "TEXT",
    "caption_source": "TEXT",
}


def _ensure_videos_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    for column, sql_type in _VIDEOS_MIGRATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {column} {sql_type}")


def _content_slots_needs_migration(conn: sqlite3.Connection) -> bool:
    """True if content_slots was created under either pre-Milestone-1.3
    schema this rebuilds away from: the old UNIQUE(scheduled_at, pillar_key)
    constraint (Milestone 1.1), or a NOT NULL pillar_key (pre-Milestone 1.3 —
    FIFO slots need pillar_key nullable). Both are fixed by the same rebuild
    pass below, run at most once regardless of which (or both) applied."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'content_slots'"
    ).fetchone()
    sql = (row["sql"] or "") if row is not None else ""
    return "UNIQUE(scheduled_at, pillar_key)" in sql or "pillar_key TEXT NOT NULL" in sql


def _migrate_content_slots_unique_constraint(conn: sqlite3.Connection) -> None:
    """Rebuild content_slots under the current schema (UNIQUE(scheduled_at)
    alone, pillar_key nullable).

    SQLite cannot alter a table's constraints in place, so this renames the
    old table, creates the new one, and copies rows across — keeping at most
    one row per scheduled_at. If a timestamp somehow has more than one row
    under the old (scheduled_at, pillar_key) constraint, the row with the
    most "advanced" status wins (PUBLISHED > ASSIGNED > OPEN/FAILED), tied
    by lowest id, so an already-assigned/published slot is never silently
    discarded in favor of a still-open duplicate. Existing rows already have
    non-null pillar_key values, so relaxing that constraint doesn't change
    any copied data — it only makes the column newly insertable as NULL.

    IMPORTANT — PRAGMA legacy_alter_table: by default (legacy_alter_table
    OFF, the modern SQLite behavior), `ALTER TABLE content_slots RENAME TO
    content_slots_old` automatically rewrites any REFERENCES clause in
    *other* tables that pointed at "content_slots" to say "content_slots_old"
    instead — including videos.assigned_slot_id. Dropping content_slots_old
    afterward then leaves that FK dangling, pointing at a table that no
    longer exists (this is exactly the bug _repair_videos_assigned_slot_fk
    fixes for a database that already migrated before this guard existed).
    legacy_alter_table=ON suppresses that cross-table rewrite entirely, so
    videos' FK text is left untouched (still "content_slots") by this
    rename — verified empirically, not merely asserted from documentation.

    Runs inside one transaction: either the whole rebuild lands, or none of
    it does. PRAGMA foreign_keys can only be toggled outside a pending
    transaction (SQLite treats it as a no-op mid-transaction), so it — and
    legacy_alter_table — must be set before BEGIN and restored after COMMIT.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE content_slots RENAME TO content_slots_old")
            # NOT executescript(): Connection.executescript() implicitly
            # COMMITs any pending transaction before running, regardless of
            # isolation_level - which would silently end our BEGIN IMMEDIATE
            # here (SCHEMA_CONTENT_SLOTS is one statement, so plain execute()
            # is both correct and safe inside the transaction).
            conn.execute(SCHEMA_CONTENT_SLOTS)

            rows = conn.execute("SELECT * FROM content_slots_old ORDER BY scheduled_at, id").fetchall()
            kept: dict[str, sqlite3.Row] = {}
            for row in rows:
                key = row["scheduled_at"]
                current = kept.get(key)
                if current is None or _SLOT_STATUS_PRIORITY.get(row["status"], 0) > _SLOT_STATUS_PRIORITY.get(current["status"], 0):
                    kept[key] = row

            for row in kept.values():
                conn.execute(
                    """
                    INSERT INTO content_slots
                        (id, scheduled_at, pillar_key, prompt, status, assigned_video_id, google_calendar_event_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], row["scheduled_at"], row["pillar_key"], row["prompt"],
                        row["status"], row["assigned_video_id"], row["google_calendar_event_id"], row["created_at"],
                    ),
                )
            conn.execute("DROP TABLE content_slots_old")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")

    dropped = len(rows) - len(kept)
    if dropped:
        print(
            f"[content_store] migrated content_slots to UNIQUE(scheduled_at): "
            f"consolidated {dropped} duplicate-datetime row(s) from the old "
            f"(scheduled_at, pillar_key) constraint, keeping the most-advanced "
            f"status per timestamp.",
            file=sys.stderr,
        )


def _videos_fk_needs_repair(conn: sqlite3.Connection) -> bool:
    """True if videos.assigned_slot_id's foreign key points at anything
    other than content_slots — e.g. the stale "content_slots_old" name left
    behind by SQLite's automatic FK-reference rewrite during an earlier
    RENAME TABLE-based content_slots migration, before the
    legacy_alter_table guard above existed. Checked structurally via PRAGMA
    foreign_key_list rather than string-matching the stored CREATE TABLE
    SQL, so it doesn't depend on the exact stale name."""
    for row in conn.execute("PRAGMA foreign_key_list(videos)").fetchall():
        if row["from"] == "assigned_slot_id" and row["table"] != "content_slots":
            return True
    return False


def _repair_videos_assigned_slot_fk(conn: sqlite3.Connection) -> None:
    """Repair a videos table whose assigned_slot_id foreign key was left
    pointing at a stale/nonexistent table name (see
    _videos_fk_needs_repair). SQLite has no ALTER TABLE ... ALTER COLUMN /
    DROP CONSTRAINT to fix a REFERENCES clause in place, so this rebuilds
    videos the same way _migrate_content_slots_unique_constraint rebuilds
    content_slots: rename, recreate under the current (correct) schema,
    copy every row across unchanged, drop the renamed original.

    legacy_alter_table=ON is required here for the same reason as that
    other migration, just mirrored: without it, `ALTER TABLE videos RENAME
    TO videos_old` would itself rewrite content_slots.assigned_video_id's
    REFERENCES clause to "videos_old", trading this bug for the same bug on
    the other table. Verified empirically that legacy_alter_table=ON
    prevents that rewrite.

    Column list is derived from the freshly (re)created table via PRAGMA
    table_info rather than hardcoded, so it stays correct as videos gains
    columns over time (_ensure_videos_columns) without this function
    needing to track them separately. Runs inside one transaction; verifies
    PRAGMA foreign_key_check is clean afterward as a hard safety check.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE videos RENAME TO videos_old")
            # NOT executescript() — see the matching note in
            # _migrate_content_slots_unique_constraint: it implicitly
            # COMMITs a pending transaction, which would silently end this
            # BEGIN IMMEDIATE. SCHEMA_VIDEOS is one statement.
            conn.execute(SCHEMA_VIDEOS)
            _ensure_videos_columns(conn)

            columns = [row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()]
            column_list = ", ".join(columns)
            conn.execute(f"INSERT INTO videos ({column_list}) SELECT {column_list} FROM videos_old")

            conn.execute("DROP TABLE videos_old")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"content_store: videos.assigned_slot_id repair left "
            f"{len(violations)} foreign_key_check violation(s): {[dict(v) for v in violations]!r}"
        )

    print(
        "[content_store] repaired videos.assigned_slot_id: it referenced a stale table name "
        "left behind by an earlier content_slots rename migration; it now correctly "
        "references content_slots.",
        file=sys.stderr,
    )


@dataclass
class VideoRecord:
    id: int
    file_hash: str
    original_filename: str
    original_path: str
    canonical_media_path: str | None
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration_seconds: float | None
    file_size_bytes: int | None
    transcript: str | None
    transcript_language: str | None
    transcription_status: str | None
    classified_pillar: str | None
    classification_confidence: float | None
    classification_reason: str | None
    classification_second_score: float | None
    classification_margin: float | None
    classifier: str | None
    status: str
    failure_reason: str | None
    assigned_slot_id: int | None
    created_at: str
    processed_at: str | None
    caption_text: str | None
    caption_source: str | None


@dataclass
class SlotRecord:
    id: int
    scheduled_at: str
    pillar_key: str | None
    prompt: str | None
    status: str
    assigned_video_id: int | None
    google_calendar_event_id: str | None
    created_at: str


@dataclass
class PlatformPostRecord:
    id: int
    video_id: int
    platform: str
    status: str
    platform_post_id: str | None
    scheduled_at: str | None
    published_at: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str


def _row_to_video(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(**dict(row))


def _row_to_slot(row: sqlite3.Row) -> SlotRecord:
    return SlotRecord(**dict(row))


def _row_to_platform_post(row: sqlite3.Row) -> PlatformPostRecord:
    return PlatformPostRecord(**dict(row))


class ContentStore:
    """Thin wrapper around a single SQLite connection for this pipeline."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_VIDEOS)
        _ensure_videos_columns(self._conn)
        if _videos_fk_needs_repair(self._conn):
            _repair_videos_assigned_slot_fk(self._conn)
        if _content_slots_needs_migration(self._conn):
            _migrate_content_slots_unique_constraint(self._conn)
        self._conn.executescript(SCHEMA_CONTENT_SLOTS)
        self._conn.executescript(SCHEMA_PLATFORM_POSTS)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ContentStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        """Wrap a block of writes in a single atomic transaction."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- videos ---------------------------------------------------------

    def get_video_by_hash(self, file_hash: str) -> VideoRecord | None:
        row = self._conn.execute(
            "SELECT * FROM videos WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return _row_to_video(row) if row else None

    def get_video(self, video_id: int) -> VideoRecord | None:
        row = self._conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _row_to_video(row) if row else None

    def get_video_by_path(self, original_path: str) -> VideoRecord | None:
        """Look up a video by its original discovery path.

        Used only for FIFO ordering (see process_content.discover_videos):
        a video already known to the store sorts by its immutable
        created_at instead of the current (possibly touched/copied) file
        mtime. Scoped to path stability — a renamed file is not matched
        here and is treated as newly discovered; see
        docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md.
        """
        row = self._conn.execute(
            "SELECT * FROM videos WHERE original_path = ?", (original_path,)
        ).fetchone()
        return _row_to_video(row) if row else None

    def insert_video(self, file_hash: str, original_filename: str, original_path: str, created_at: str) -> VideoRecord:
        cur = self._conn.execute(
            """
            INSERT INTO videos (file_hash, original_filename, original_path, status, created_at)
            VALUES (?, ?, ?, 'DISCOVERED', ?)
            """,
            (file_hash, original_filename, original_path, created_at),
        )
        return self.get_video_by_hash(file_hash) or _raise_missing(cur.lastrowid)

    def update_video(self, video_id: int, **fields) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), video_id]
        self._conn.execute(f"UPDATE videos SET {columns} WHERE id = ?", values)

    # -- content_slots ----------------------------------------------------

    def insert_slot_if_missing(
        self,
        scheduled_at: str,
        pillar_key: str | None,
        prompt: str | None,
        created_at: str,
        google_calendar_event_id: str | None = None,
    ) -> bool:
        """Insert a content_slot unless one already exists for this scheduled_at.

        Returns True if a new row was created, False if it already existed —
        this is what keeps re-running generate_calendar.py for the same month
        from duplicating slots, and what stops a changed strategy from
        silently overwriting an existing slot's pillar/prompt: the row already
        there always wins, regardless of what the current config would now
        compute for that timestamp.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO content_slots
                (scheduled_at, pillar_key, prompt, status, google_calendar_event_id, created_at)
            VALUES (?, ?, ?, 'OPEN', ?, ?)
            """,
            (scheduled_at, pillar_key, prompt, google_calendar_event_id, created_at),
        )
        return cur.rowcount > 0

    def get_slot(self, slot_id: int) -> SlotRecord | None:
        row = self._conn.execute("SELECT * FROM content_slots WHERE id = ?", (slot_id,)).fetchone()
        return _row_to_slot(row) if row else None

    def find_earliest_open_slot(self, pillar_key: str, after_iso: str) -> SlotRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM content_slots
            WHERE pillar_key = ? AND status = 'OPEN' AND scheduled_at > ?
            ORDER BY scheduled_at ASC
            LIMIT 1
            """,
            (pillar_key, after_iso),
        ).fetchone()
        return _row_to_slot(row) if row else None

    def find_earliest_open_slot_fifo(self, after_iso: str) -> SlotRecord | None:
        """Earliest OPEN slot at or after after_iso, regardless of pillar_key.
        Inclusive (>=), matching the FIFO matching contract in
        docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md —
        deliberately different from find_earliest_open_slot's exclusive (>),
        which stays unchanged for pillar mode."""
        row = self._conn.execute(
            """
            SELECT * FROM content_slots
            WHERE status = 'OPEN' AND scheduled_at >= ?
            ORDER BY scheduled_at ASC
            LIMIT 1
            """,
            (after_iso,),
        ).fetchone()
        return _row_to_slot(row) if row else None

    def list_slots_by_status(self, statuses: list[str]) -> list[SlotRecord]:
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM content_slots WHERE status IN ({placeholders}) ORDER BY scheduled_at",
            statuses,
        ).fetchall()
        return [_row_to_slot(row) for row in rows]

    def delete_slot(self, slot_id: int) -> None:
        """Delete a content_slot row. Raises an sqlite3 IntegrityError (FK
        violation) if a video still references it via assigned_slot_id —
        call unassign_video_for_slot(slot_id) first for an ASSIGNED slot."""
        self._conn.execute("DELETE FROM content_slots WHERE id = ?", (slot_id,))

    def unassign_video_for_slot(self, slot_id: int) -> None:
        """Reset any video assigned to this slot back to CLASSIFIED with no
        assignment, so the slot can be deleted and the video can be routed
        to a different slot on a future process_content.py run. Used by
        clear_calendar.py --all; does not touch the video's transcript or
        classification, only its scheduling state."""
        self._conn.execute(
            "UPDATE videos SET assigned_slot_id = NULL, status = 'CLASSIFIED', processed_at = NULL "
            "WHERE assigned_slot_id = ?",
            (slot_id,),
        )

    def assign_slot(self, video_id: int, slot_id: int) -> None:
        """Atomically claim a slot for a video. Raises if the slot is no longer OPEN."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM content_slots WHERE id = ?", (slot_id,)
            ).fetchone()
            if row is None or row["status"] != "OPEN":
                raise SlotUnavailableError(f"content_slot {slot_id} is not OPEN")
            conn.execute(
                "UPDATE content_slots SET status = 'ASSIGNED', assigned_video_id = ? WHERE id = ?",
                (video_id, slot_id),
            )
            conn.execute(
                "UPDATE videos SET assigned_slot_id = ? WHERE id = ?",
                (slot_id, video_id),
            )

    # -- platform_posts (Milestone 2.0) ----------------------------------

    def get_platform_post(self, video_id: int, platform: str) -> PlatformPostRecord | None:
        row = self._conn.execute(
            "SELECT * FROM platform_posts WHERE video_id = ? AND platform = ?", (video_id, platform)
        ).fetchone()
        return _row_to_platform_post(row) if row else None

    def insert_platform_post(
        self, video_id: int, platform: str, created_at: str, scheduled_at: str | None = None
    ) -> PlatformPostRecord:
        """Create the one publishing record for this (video, platform) pair.

        Raises sqlite3.IntegrityError (UNIQUE(video_id, platform)) if one
        already exists — callers must check get_platform_post() first and
        update the existing row instead; this is the idempotency guard
        against accidentally submitting the same post twice, enforced by
        the schema rather than only by caller discipline.
        """
        self._conn.execute(
            """
            INSERT INTO platform_posts (video_id, platform, status, scheduled_at, created_at, updated_at)
            VALUES (?, ?, 'PENDING', ?, ?, ?)
            """,
            (video_id, platform, scheduled_at, created_at, created_at),
        )
        return self.get_platform_post(video_id, platform)

    def update_platform_post(self, post_id: int, updated_at: str, **fields) -> None:
        fields = {**fields, "updated_at": updated_at}
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), post_id]
        self._conn.execute(f"UPDATE platform_posts SET {columns} WHERE id = ?", values)


class SlotUnavailableError(Exception):
    """Raised when a slot is claimed between selection and assignment."""


def _raise_missing(lastrowid: int):
    raise RuntimeError(f"insert succeeded but row {lastrowid} could not be re-read")
