"""
content_store.py — SQLite persistence for videos and content_slots.

What it does:
  Owns the two tables that make the content-processing pipeline restart-safe
  and idempotent: `videos` (one row per ingested file, keyed by content hash)
  and `content_slots` (one row per calendar posting slot, written by
  generate_calendar.py and consumed by process_content.py / slot_matcher.py).

  Google Calendar remains the source of truth for what gets posted when;
  content_slots is an internal mirror that lets process_content.py query and
  atomically claim open slots without re-deriving the schedule.

Dependencies:
  stdlib sqlite3 only.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config import DB_PATH

SCHEMA = """
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
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    failure_reason TEXT,
    assigned_slot_id INTEGER REFERENCES content_slots(id),
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS content_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_at TEXT NOT NULL,
    pillar_key TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    assigned_video_id INTEGER REFERENCES videos(id),
    google_calendar_event_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(scheduled_at, pillar_key)
);
"""


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
    status: str
    failure_reason: str | None
    assigned_slot_id: int | None
    created_at: str
    processed_at: str | None


@dataclass
class SlotRecord:
    id: int
    scheduled_at: str
    pillar_key: str
    prompt: str
    status: str
    assigned_video_id: int | None
    google_calendar_event_id: str | None
    created_at: str


def _row_to_video(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(**dict(row))


def _row_to_slot(row: sqlite3.Row) -> SlotRecord:
    return SlotRecord(**dict(row))


class ContentStore:
    """Thin wrapper around a single SQLite connection for this pipeline."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

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
        pillar_key: str,
        prompt: str,
        created_at: str,
        google_calendar_event_id: str | None = None,
    ) -> bool:
        """Insert a content_slot unless one already exists for (scheduled_at, pillar_key).

        Returns True if a new row was created, False if it already existed —
        this is what keeps re-running generate_calendar.py for the same month
        from duplicating slot records.
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


class SlotUnavailableError(Exception):
    """Raised when a slot is claimed between selection and assignment."""


def _raise_missing(lastrowid: int):
    raise RuntimeError(f"insert succeeded but row {lastrowid} could not be re-read")
