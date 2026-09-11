"""Idempotency tests for content_store.py."""

import sqlite3

import pytest

from content_store import ContentStore


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


def test_insert_slot_if_missing_is_idempotent(store):
    """Re-running generate_calendar.py for the same month must not duplicate slots."""
    created_first = store.insert_slot_if_missing(
        "2026-09-14T10:00:00", "building", "prompt A", "2026-09-01T00:00:00"
    )
    created_second = store.insert_slot_if_missing(
        "2026-09-14T10:00:00", "building", "prompt A (regenerated)", "2026-09-02T00:00:00"
    )

    assert created_first is True
    assert created_second is False

    slot = store.find_earliest_open_slot("building", "2000-01-01T00:00:00")
    assert slot.prompt == "prompt A"  # first insert wins; rerun did not overwrite it


def test_get_video_by_hash_returns_none_when_absent(store):
    assert store.get_video_by_hash("does-not-exist") is None


def test_insert_video_then_lookup_by_hash_is_idempotent_identity(store):
    """process_content.py calls get_video_by_hash before insert_video on every run;
    the same physical file (same hash) must resolve to the same DB row."""
    created = store.insert_video("abc123", "video.mp4", "/incoming/video.mp4", "2026-09-01T00:00:00")

    found = store.get_video_by_hash("abc123")

    assert found.id == created.id
    assert found.status == "DISCOVERED"


def test_update_video_persists_fields(store):
    video = store.insert_video("abc123", "video.mp4", "/incoming/video.mp4", "2026-09-01T00:00:00")

    store.update_video(video.id, status="VALIDATED", container="mov", video_codec="hevc")

    updated = store.get_video_by_hash("abc123")
    assert updated.status == "VALIDATED"
    assert updated.container == "mov"
    assert updated.video_codec == "hevc"


def test_opening_a_database_with_the_old_unique_constraint_migrates_it(tmp_path):
    """Milestone 1.1 changed content_slots' uniqueness from
    (scheduled_at, pillar_key) to scheduled_at alone. A database created
    under the old constraint must be upgraded automatically and safely the
    first time it is opened, keeping the most-advanced-status row when a
    timestamp has duplicates under the old constraint."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE content_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_at TEXT NOT NULL,
            pillar_key TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            assigned_video_id INTEGER,
            google_calendar_event_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(scheduled_at, pillar_key)
        )
        """
    )
    # Two rows sharing a scheduled_at under the old constraint (only possible
    # because their pillar_key differs) — the ASSIGNED one must survive.
    conn.execute(
        "INSERT INTO content_slots (scheduled_at, pillar_key, prompt, status, created_at) "
        "VALUES ('2026-09-14T09:00:00', 'building', 'old prompt', 'OPEN', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO content_slots (scheduled_at, pillar_key, prompt, status, created_at) "
        "VALUES ('2026-09-14T09:00:00', 'acquisition', 'other prompt', 'ASSIGNED', '2026-01-02T00:00:00')"
    )
    conn.execute(
        "INSERT INTO content_slots (scheduled_at, pillar_key, prompt, status, created_at) "
        "VALUES ('2026-09-21T09:00:00', 'mindset', 'unique row', 'OPEN', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    with ContentStore(db_path=db_path) as store:
        rows = store._conn.execute("SELECT * FROM content_slots ORDER BY scheduled_at").fetchall()

        assert len(rows) == 2  # duplicate-datetime pair consolidated to one
        assert rows[0]["pillar_key"] == "acquisition"  # ASSIGNED row won over OPEN
        assert rows[0]["status"] == "ASSIGNED"
        assert rows[1]["pillar_key"] == "mindset"

        # New constraint is now enforced going forward.
        created = store.insert_slot_if_missing(
            "2026-09-21T09:00:00", "building", "new prompt", "2026-02-01T00:00:00"
        )
        assert created is False
