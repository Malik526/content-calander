"""Idempotency tests for content_store.py."""

import sqlite3

import pytest

from content_store import ContentStore, _videos_fk_needs_repair


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


def test_opening_a_database_missing_classifier_columns_adds_them(tmp_path):
    """Milestone 1.2 added classification_second_score/classification_margin/
    classifier to videos. A database created before that must gain these
    columns (nullable, existing rows unaffected) the first time it's opened,
    without losing any existing data."""
    db_path = tmp_path / "legacy_videos.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE videos (
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
            assigned_slot_id INTEGER,
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO videos (file_hash, original_filename, original_path, status, created_at) "
        "VALUES ('abc', 'video.mp4', '/incoming/video.mp4', 'DISCOVERED', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    with ContentStore(db_path=db_path) as store:
        video = store.get_video_by_hash("abc")

        assert video.classification_second_score is None
        assert video.classification_margin is None
        assert video.classifier is None

        store.update_video(video.id, classification_second_score=0.4, classification_margin=0.12, classifier="embeddings")
        updated = store.get_video_by_hash("abc")
        assert updated.classification_margin == 0.12
        assert updated.classifier == "embeddings"


def test_list_slots_by_status_filters_correctly(store):
    store.insert_slot_if_missing("2026-09-01T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    store.insert_slot_if_missing("2026-09-02T09:00:00", "career", "p", "2026-01-01T00:00:00")
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot("career", "2000-01-01T00:00:00")
    store.assign_slot(video.id, slot.id)

    open_slots = store.list_slots_by_status(["OPEN"])
    assigned_slots = store.list_slots_by_status(["ASSIGNED"])

    assert len(open_slots) == 1
    assert open_slots[0].pillar_key == "engineering"
    assert len(assigned_slots) == 1
    assert assigned_slots[0].pillar_key == "career"


def test_delete_slot_removes_open_slot(store):
    store.insert_slot_if_missing("2026-09-01T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00")

    store.delete_slot(slot.id)

    assert store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00") is None


def test_delete_slot_referenced_by_video_raises_without_unassign(store):
    store.insert_slot_if_missing("2026-09-01T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00")
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    store.assign_slot(video.id, slot.id)

    with pytest.raises(sqlite3.IntegrityError):
        store.delete_slot(slot.id)


def test_unassign_video_for_slot_then_delete_succeeds(store):
    store.insert_slot_if_missing("2026-09-01T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    slot = store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00")
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
    store.assign_slot(video.id, slot.id)

    store.unassign_video_for_slot(slot.id)
    store.delete_slot(slot.id)  # must not raise now

    updated_video = store.get_video_by_hash("h1")
    assert updated_video.assigned_slot_id is None
    assert updated_video.status == "CLASSIFIED"
    assert updated_video.transcript is None  # unassign never touches transcript/classification data it doesn't own


# ---------------------------------------------------------------------------
# FIFO slots: nullable pillar_key (Milestone 1.3)
# ---------------------------------------------------------------------------

def test_insert_slot_if_missing_accepts_null_pillar_key(store):
    created = store.insert_slot_if_missing("2026-09-01T09:00:00", None, None, "2026-01-01T00:00:00")
    assert created is True

    slot = store.find_earliest_open_slot_fifo("2000-01-01T00:00:00")
    assert slot is not None
    assert slot.pillar_key is None


def test_find_earliest_open_slot_fifo_ignores_pillar(store):
    store.insert_slot_if_missing("2026-09-01T09:00:00", "engineering", "p", "2026-01-01T00:00:00")
    store.insert_slot_if_missing("2026-09-02T09:00:00", None, None, "2026-01-01T00:00:00")

    slot = store.find_earliest_open_slot_fifo("2000-01-01T00:00:00")

    assert slot.scheduled_at == "2026-09-01T09:00:00"  # earliest wins regardless of pillar_key


def test_find_earliest_open_slot_fifo_inclusive_boundary(store):
    """FIFO matching is scheduled_at >= after (inclusive), unlike the
    pillar matcher's exclusive >."""
    store.insert_slot_if_missing("2026-09-01T09:00:00", None, None, "2026-01-01T00:00:00")

    slot = store.find_earliest_open_slot_fifo("2026-09-01T09:00:00")  # exactly at boundary

    assert slot is not None


def test_find_earliest_open_slot_fifo_returns_none_when_nothing_open(store):
    assert store.find_earliest_open_slot_fifo("2000-01-01T00:00:00") is None


def test_opening_a_database_with_not_null_pillar_key_migrates_it(tmp_path):
    """Pre-Milestone-1.3 databases have pillar_key TEXT NOT NULL. Opening
    one must relax that constraint (so FIFO slots can be inserted) without
    losing any existing rows."""
    db_path = tmp_path / "legacy_not_null.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE content_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_at TEXT NOT NULL UNIQUE,
            pillar_key TEXT NOT NULL,
            prompt TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            assigned_video_id INTEGER,
            google_calendar_event_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO content_slots (scheduled_at, pillar_key, prompt, status, created_at) "
        "VALUES ('2026-09-14T09:00:00', 'building', 'old prompt', 'OPEN', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    with ContentStore(db_path=db_path) as store:
        rows = store._conn.execute("SELECT * FROM content_slots").fetchall()
        assert len(rows) == 1
        assert rows[0]["pillar_key"] == "building"  # existing row preserved

        created = store.insert_slot_if_missing("2026-09-21T09:00:00", None, None, "2026-02-01T00:00:00")
        assert created is True  # NULL pillar_key now accepted


# ---------------------------------------------------------------------------
# FIFO ordering: get_video_by_path (Milestone 1.3)
# ---------------------------------------------------------------------------

def test_get_video_by_path_returns_none_when_absent(store):
    assert store.get_video_by_path("/incoming/does-not-exist.mp4") is None


def test_get_video_by_path_finds_video_by_original_path(store):
    video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")

    found = store.get_video_by_path("/incoming/v.mp4")

    assert found.id == video.id


# ---------------------------------------------------------------------------
# videos.assigned_slot_id FK repair
#
# A real database hit `sqlite3.OperationalError: no such table:
# main.content_slots_old` in insert_video(). Root cause (confirmed against
# the real database, not assumed from the traceback): SQLite's modern
# ALTER TABLE RENAME automatically rewrites REFERENCES clauses in *other*
# tables when the table they reference is renamed. The pre-existing
# `_migrate_content_slots_unique_constraint` renames content_slots to
# content_slots_old mid-rebuild; that rewrote videos.assigned_slot_id's
# REFERENCES clause to "content_slots_old", and dropping the temporary
# table then left it dangling. Fixed two ways: the rename migration now
# runs under PRAGMA legacy_alter_table=ON so it can never do this again,
# and _repair_videos_assigned_slot_fk() detects and fixes a database that
# already migrated before that guard existed.
# ---------------------------------------------------------------------------

def _assigned_slot_fk_table(conn: sqlite3.Connection) -> str | None:
    for row in conn.execute("PRAGMA foreign_key_list(videos)").fetchall():
        if row["from"] == "assigned_slot_id":
            return row["table"]
    return None


def test_videos_fk_needs_repair_detects_stale_reference(tmp_path):
    conn = sqlite3.connect(tmp_path / "detect.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE content_slots (id INTEGER PRIMARY KEY)")
    conn.execute('CREATE TABLE videos (id INTEGER PRIMARY KEY, assigned_slot_id INTEGER REFERENCES "content_slots_old"(id))')
    assert _videos_fk_needs_repair(conn) is True
    conn.close()


def test_videos_fk_needs_repair_false_for_correct_reference(tmp_path):
    conn = sqlite3.connect(tmp_path / "correct.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE content_slots (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, assigned_slot_id INTEGER REFERENCES content_slots(id))")
    assert _videos_fk_needs_repair(conn) is False
    conn.close()


def test_fresh_database_never_triggers_fk_repair(tmp_path, capsys):
    """Case 1 — fresh current-schema DB: opens normally, no repair runs, insert succeeds."""
    with ContentStore(db_path=tmp_path / "fresh.db") as store:
        captured = capsys.readouterr()
        assert "repaired videos.assigned_slot_id" not in captured.err

        assert _assigned_slot_fk_table(store._conn) == "content_slots"

        video = store.insert_video("h1", "v.mp4", "/incoming/v.mp4", "2026-01-01T00:00:00")
        assert video.status == "DISCOVERED"


def test_old_pre_1_3_db_migration_never_corrupts_videos_fk(tmp_path):
    """Case 2 — old pre-Milestone-1.3 DB: content_slots has the old
    (scheduled_at, pillar_key) uniqueness and videos.assigned_slot_id
    already correctly references content_slots. Opening ContentStore
    upgrades content_slots as before, and must NOT rewrite videos' FK to
    "content_slots_old" as a side effect (the legacy_alter_table guard)."""
    db_path = tmp_path / "legacy_full.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE content_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_at TEXT NOT NULL,
            pillar_key TEXT NOT NULL,
            prompt TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            assigned_video_id INTEGER REFERENCES videos(id),
            google_calendar_event_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(scheduled_at, pillar_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE videos (
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
        )
        """
    )
    conn.execute(
        "INSERT INTO content_slots (id, scheduled_at, pillar_key, status, created_at) "
        "VALUES (1, '2026-09-14T09:00:00', 'building', 'ASSIGNED', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO videos (id, file_hash, original_filename, original_path, status, assigned_slot_id, created_at, processed_at) "
        "VALUES (1, 'h1', 'v1.mp4', '/incoming/v1.mp4', 'ASSIGNED', 1, '2026-01-01T00:00:00', '2026-01-02T00:00:00')"
    )
    conn.commit()
    conn.close()

    with ContentStore(db_path=db_path) as store:
        assert _assigned_slot_fk_table(store._conn) == "content_slots"
        assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # existing slot/video relationship preserved
        video = store.get_video_by_hash("h1")
        assert video.assigned_slot_id == 1

        # the exact operation that crashed for real must succeed
        new_video = store.insert_video("h2", "v2.mp4", "/incoming/v2.mp4", "2026-02-01T00:00:00")
        assert new_video.status == "DISCOVERED"


def _make_broken_videos_fk_db(db_path) -> None:
    """Reproduces the real corrupted state: content_slots exists under the
    current schema, but videos.assigned_slot_id's REFERENCES clause still
    names "content_slots_old", which does not exist as a table at all —
    exactly what a real database looked like after the pre-fix
    _migrate_content_slots_unique_constraint ran."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE content_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_at TEXT NOT NULL UNIQUE,
            pillar_key TEXT,
            prompt TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            assigned_video_id INTEGER REFERENCES videos(id),
            google_calendar_event_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE videos (
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
            assigned_slot_id INTEGER REFERENCES "content_slots_old"(id),
            created_at TEXT NOT NULL,
            processed_at TEXT,
            caption_text TEXT,
            caption_source TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO content_slots (id, scheduled_at, pillar_key, status, created_at) "
        "VALUES (1, '2026-09-14T09:00:00', NULL, 'ASSIGNED', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO content_slots (id, scheduled_at, pillar_key, status, created_at) "
        "VALUES (2, '2026-09-21T09:00:00', NULL, 'OPEN', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO videos "
        "(id, file_hash, original_filename, original_path, transcript, caption_text, caption_source, "
        " status, assigned_slot_id, created_at, processed_at) "
        "VALUES (1, 'h1', 'v1.mp4', '/incoming/v1.mp4', 'hello world', 'hello world', 'transcript_auto', "
        "'ASSIGNED', 1, '2026-01-01T00:00:00', '2026-01-02T00:00:00')"
    )
    conn.execute(
        "INSERT INTO videos (id, file_hash, original_filename, original_path, status, created_at) "
        "VALUES (2, 'h2', 'v2.mp4', '/incoming/v2.mp4', 'TRANSCRIBED', '2026-01-03T00:00:00')"
    )
    conn.commit()
    conn.close()


def test_opening_an_already_broken_database_repairs_the_fk(tmp_path, capsys):
    """Case 3 — already-broken migrated DB: opening ContentStore detects
    and repairs it, preserving every existing row, leaving the FK pointing
    at content_slots, PRAGMA foreign_key_check clean, and insert_video()
    (the exact operation that crashed for real) succeeding afterward."""
    db_path = tmp_path / "broken_fk.db"
    _make_broken_videos_fk_db(db_path)

    with ContentStore(db_path=db_path) as store:
        captured = capsys.readouterr()
        assert "repaired videos.assigned_slot_id" in captured.err

        assert _assigned_slot_fk_table(store._conn) == "content_slots"
        assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []

        v1 = store.get_video_by_hash("h1")
        assert v1.id == 1
        assert v1.transcript == "hello world"
        assert v1.caption_text == "hello world"
        assert v1.caption_source == "transcript_auto"
        assert v1.status == "ASSIGNED"
        assert v1.assigned_slot_id == 1
        assert v1.processed_at == "2026-01-02T00:00:00"

        v2 = store.get_video_by_hash("h2")
        assert v2.id == 2
        assert v2.assigned_slot_id is None

        slots = store._conn.execute("SELECT * FROM content_slots ORDER BY id").fetchall()
        assert [s["id"] for s in slots] == [1, 2]
        assert slots[0]["status"] == "ASSIGNED"
        assert slots[1]["status"] == "OPEN"

        new_video = store.insert_video("h3", "v3.mp4", "/incoming/v3.mp4", "2026-03-01T00:00:00")
        assert new_video.status == "DISCOVERED"


def test_fk_repair_is_idempotent_on_reopen(tmp_path, capsys):
    """Case 4 — idempotency: reopening an already-repaired database does
    not run the repair again or alter data, and the schema stays correct."""
    db_path = tmp_path / "broken_fk_reopen.db"
    _make_broken_videos_fk_db(db_path)

    with ContentStore(db_path=db_path):
        pass  # first open performs the repair
    capsys.readouterr()  # discard first-open output

    with ContentStore(db_path=db_path) as store:
        captured = capsys.readouterr()
        assert "repaired videos.assigned_slot_id" not in captured.err

        assert _assigned_slot_fk_table(store._conn) == "content_slots"
        assert store._conn.execute("PRAGMA foreign_key_check").fetchall() == []

        v1 = store.get_video_by_hash("h1")
        assert v1.transcript == "hello world"
        assert v1.assigned_slot_id == 1

        new_video = store.insert_video("h4", "v4.mp4", "/incoming/v4.mp4", "2026-04-01T00:00:00")
        assert new_video.status == "DISCOVERED"
