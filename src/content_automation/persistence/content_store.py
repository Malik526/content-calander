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
from datetime import datetime, timezone
from pathlib import Path

from content_automation.config import DB_PATH


def _utc_now_iso() -> str:
    """Aware-UTC isoformat timestamp — the same convention every other
    aware-UTC write in this codebase uses (updated_at, published_at,
    next_status_check_at; see publish_tiktok._now_iso/worker._now_iso).
    Used only by the get-or-create helpers below, which are the one place
    ContentStore itself originates a timestamp rather than receiving one
    from a caller — every other write method still takes created_at/
    updated_at as an explicit parameter, unchanged."""
    return datetime.now(timezone.utc).isoformat()

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

# Added Milestone 3.2 (user authentication / ownership model — see
# docs/decisions/0007-user-ownership-model.md and
# docs/architecture/hosted-product-boundary.md). Three new tables,
# deliberately additive and independent of the existing schema:
#
#   users              — the canonical internal Pickle Batch identity.
#                         Never keyed by an external provider ID; external
#                         identities map onto it via auth_identities below.
#   auth_identities     — one row per (external auth provider, external
#                         subject) a user has signed in with. Kept separate
#                         from `users` from day one so a single Pickle Batch
#                         account can later have more than one linked
#                         provider (Google, Apple, email/magic-link)
#                         without a schema change.
#   platform_connections — one row per (user, publishing platform) —
#                         identity/status metadata only (e.g. TikTok
#                         open_id). Deliberately does NOT store credential
#                         secrets: the real TikTok access/refresh token
#                         stays exactly where it already lives
#                         (config.TIKTOK_TOKEN_PATH, a single local file) —
#                         moving it into this table, or into Postgres, is
#                         explicitly out of this milestone's scope. See
#                         "Current Local Credential Bridge" in the
#                         evaluation record for how the one real existing
#                         TikTok credential maps onto this model today.
#
# No user_id anywhere in this schema is enforced NOT NULL at the SQL level
# — SQLite cannot add a NOT NULL column with a FOREIGN KEY to an existing
# populated table without the same rename/rebuild dance already used for
# content_slots/videos above, and forcing that risk onto real production
# rows was judged not worth it for a nullable-by-transition column. The
# invariant is enforced at the application layer instead (every write path
# that matters is exercised with an explicit user_id; ContentStore's own
# ownership-consistency check in assign_slot() below is the one place two
# already-written rows' ownership is cross-checked) — see
# docs/architecture/hosted-product-boundary.md for why this is judged
# acceptable under SQLite and what changes under the Postgres migration.
SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SCHEMA_AUTH_IDENTITIES = """
CREATE TABLE IF NOT EXISTS auth_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    provider_subject TEXT NOT NULL,
    provider_email TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, provider_subject)
);
"""

# UNIQUE(user_id, platform): one connection per (user, platform) for V1 —
# the same "one TikTok account" shape this deployment already has today,
# rescoped to per-user rather than per-deployment. Deliberately decided,
# not incidental: if a future product tier needs multiple accounts per
# platform per user, that is a new milestone's schema change, not an
# oversight here (see docs/decisions/0007-user-ownership-model.md).
SCHEMA_PLATFORM_CONNECTIONS = """
CREATE TABLE IF NOT EXISTS platform_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    external_account_id TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, platform)
);
"""

# Added Milestone 3.6 (real authentication + hosted TikTok connection — see
# docs/decisions/0011-real-authentication-and-tiktok-connection.md).
# Deliberately its own table, not new columns on platform_connections:
# platform_connections is documented (above) to carry no credential
# secrets, and that boundary is preserved here rather than broken —
# identity/status stays in platform_connections, the actual encrypted
# access/refresh token pair lives only here. UNIQUE(platform_connection_id)
# — one credential per connection, matching platform_connections' own
# UNIQUE(user_id, platform). encrypted_payload is a Fernet ciphertext of the
# same token JSON shape publishing/tiktok/auth.py's save_token() already
# writes (access_token, refresh_token, access_token_expires_at,
# refresh_token_expires_at, open_id, scope) — see
# publishing/tiktok/credential_store.py. updated_at backs an optimistic-
# concurrency (CAS) refresh, the same update_platform_post_if_unchanged
# pattern already used elsewhere in this store, replacing tiktok_auth.py's
# fcntl-based lock for this hosted, multi-process-safe path specifically —
# the existing local-file/fcntl path for the CLI's own token is untouched.
SCHEMA_PLATFORM_CREDENTIALS = """
CREATE TABLE IF NOT EXISTS platform_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_connection_id INTEGER NOT NULL UNIQUE REFERENCES platform_connections(id),
    encrypted_payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Added Milestone 3.6. Server-side, DB-backed pending-OAuth-attempt state —
# the hosted equivalent of tiktok_auth.py's TIKTOK_PENDING_AUTH_PATH file,
# but user-bound (so a callback can only ever complete the flow it belongs
# to — see api/routes/platforms_tiktok.py) and usable across separate
# stateless API requests (connect and callback are two different HTTP
# requests, possibly handled by two different processes, unlike the local
# CLI's single long-lived process). `state` is UNIQUE so a raw duplicate
# insert is rejected outright; `consumed_at` (set exactly once, via an
# atomic CAS update — see consume_oauth_state()) makes replaying an
# already-used state a no-op rejection rather than a second successful
# completion, and `expires_at` bounds how long an abandoned attempt stays
# valid (config.OAUTH_STATE_TTL_SECONDS).
SCHEMA_OAUTH_STATES = """
CREATE TABLE IF NOT EXISTS oauth_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
"""

# The one local bootstrap identity every pre-3.2 row (and every CLI
# invocation, until a real auth layer exists) is attributed to. Not a
# secret, not exposed externally — a fixed, documented anchor, exactly the
# role config.APP_CALENDAR_SUMMARY plays for the dedicated Calendar. See
# ContentStore.get_or_create_local_user().
LOCAL_BOOTSTRAP_USER_EMAIL = "local@pickle-batch.local"

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
    # Milestone 3.2 (ownership) — see SCHEMA_USERS' docstring above for why
    # this is nullable rather than NOT NULL at the SQL level.
    "user_id": "INTEGER REFERENCES users(id)",
    # Milestone 3.4 (object storage) — both NULL means "legacy/local-direct":
    # canonical_media_path is still the authoritative local filesystem path,
    # exactly as before this milestone. Both set means canonical_media_path
    # is no longer authoritative — storage_provider/storage_key are, and
    # media.media_storage.materialize_canonical_media() resolves bytes
    # through the configured StorageProtocol backend instead. See
    # docs/decisions/0009-object-storage-media-lifecycle.md "Canonical
    # Media Reference".
    "storage_provider": "TEXT",
    "storage_key": "TEXT",
}


def _ensure_videos_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    for column, sql_type in _VIDEOS_MIGRATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {column} {sql_type}")


# New platform_posts columns added for Milestone 2.1.6 (retry classification
# and backoff — see docs/evaluations/scheduling/milestone-2.1.6-retry-backoff.md).
# retry_count defaults to 0 for every existing row (a row from before this
# milestone has never been retried); next_retry_at stays NULL until a
# retryable failure schedules one. No last_error_code column — the existing
# failure_reason already carries enough for this milestone's needs (see the
# evaluation doc for why a separate structured column wasn't justified).
_PLATFORM_POSTS_MIGRATION_COLUMNS = {
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
    "next_retry_at": "TEXT",
    # Milestone 2.1.10 (asynchronous publish reconciliation): distinct from
    # next_retry_at — next_retry_at gates re-*submitting* a not-yet-accepted
    # PENDING row; next_status_check_at gates re-*polling* a PUBLISHING row
    # TikTok has already accepted (platform_post_id set). Aware-UTC
    # isoformat, matching updated_at's convention (not scheduled_at's naive
    # local-time convention) — see reconciliation.py.
    "next_status_check_at": "TEXT",
    "status_check_count": "INTEGER NOT NULL DEFAULT 0",
    # Milestone 3.2 (ownership) — see SCHEMA_USERS' docstring above.
    "user_id": "INTEGER REFERENCES users(id)",
}


def _ensure_platform_posts_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(platform_posts)").fetchall()}
    for column, sql_type in _PLATFORM_POSTS_MIGRATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE platform_posts ADD COLUMN {column} {sql_type}")


# Milestone 3.2 (ownership) — content_slots had no prior "_ensure_*_columns"
# helper (its only prior schema change was the rebuild-based unique-
# constraint migration above); this is its first additive nullable-column
# migration, following the exact same pattern as videos/platform_posts.
_CONTENT_SLOTS_MIGRATION_COLUMNS = {
    "user_id": "INTEGER REFERENCES users(id)",
}


def _ensure_content_slots_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(content_slots)").fetchall()}
    for column, sql_type in _CONTENT_SLOTS_MIGRATION_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE content_slots ADD COLUMN {column} {sql_type}")


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
    user_id: int | None
    storage_provider: str | None
    storage_key: str | None


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
    user_id: int | None


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
    retry_count: int
    next_retry_at: str | None
    next_status_check_at: str | None
    status_check_count: int
    user_id: int | None


@dataclass
class UserRecord:
    id: int
    email: str
    display_name: str | None
    created_at: str
    updated_at: str


@dataclass
class AuthIdentityRecord:
    id: int
    user_id: int
    provider: str
    provider_subject: str
    provider_email: str | None
    created_at: str
    updated_at: str


@dataclass
class PlatformConnectionRecord:
    id: int
    user_id: int
    platform: str
    external_account_id: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass
class PlatformCredentialRecord:
    id: int
    platform_connection_id: int
    encrypted_payload: str
    created_at: str
    updated_at: str


@dataclass
class OAuthStateRecord:
    id: int
    user_id: int
    platform: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: str
    expires_at: str
    consumed_at: str | None


def _row_to_video(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(**dict(row))


def _row_to_slot(row: sqlite3.Row) -> SlotRecord:
    return SlotRecord(**dict(row))


def _row_to_platform_post(row: sqlite3.Row) -> PlatformPostRecord:
    return PlatformPostRecord(**dict(row))


def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(**dict(row))


def _row_to_auth_identity(row: sqlite3.Row) -> AuthIdentityRecord:
    return AuthIdentityRecord(**dict(row))


def _row_to_platform_connection(row: sqlite3.Row) -> PlatformConnectionRecord:
    return PlatformConnectionRecord(**dict(row))


def _row_to_platform_credential(row: sqlite3.Row) -> PlatformCredentialRecord:
    return PlatformCredentialRecord(**dict(row))


def _row_to_oauth_state(row: sqlite3.Row) -> OAuthStateRecord:
    return OAuthStateRecord(**dict(row))


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
        _ensure_content_slots_columns(self._conn)
        self._conn.executescript(SCHEMA_PLATFORM_POSTS)
        _ensure_platform_posts_columns(self._conn)
        # Milestone 3.2 (ownership): created after videos/content_slots/
        # platform_posts so the REFERENCES users(id) clauses those tables'
        # new user_id columns carry are meaningful from the first run
        # (SQLite does not require the referenced table to exist first —
        # verified directly — but creating users() first keeps the
        # dependency order obvious to a reader).
        self._conn.executescript(SCHEMA_USERS)
        self._conn.executescript(SCHEMA_AUTH_IDENTITIES)
        self._conn.executescript(SCHEMA_PLATFORM_CONNECTIONS)
        # Milestone 3.6: created after platform_connections/users so their
        # REFERENCES clauses are meaningful from the first run.
        self._conn.executescript(SCHEMA_PLATFORM_CREDENTIALS)
        self._conn.executescript(SCHEMA_OAUTH_STATES)

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

    def insert_video(
        self, file_hash: str, original_filename: str, original_path: str, created_at: str,
        user_id: int | None = None,
    ) -> VideoRecord:
        """user_id (Milestone 3.2) is optional and defaults to None (legacy/
        unscoped) purely for backward compatibility with every pre-3.2
        caller and test — every real production caller
        (media.processing.process_one) always passes the resolved local
        user's id. See docs/architecture/hosted-product-boundary.md's
        persistence-boundary section for why this stays optional at the
        ContentStore layer rather than required."""
        cur = self._conn.execute(
            """
            INSERT INTO videos (file_hash, original_filename, original_path, status, created_at, user_id)
            VALUES (?, ?, ?, 'DISCOVERED', ?, ?)
            """,
            (file_hash, original_filename, original_path, created_at, user_id),
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
        user_id: int | None = None,
    ) -> bool:
        """Insert a content_slot unless one already exists for this scheduled_at.

        Returns True if a new row was created, False if it already existed —
        this is what keeps re-running generate_calendar.py for the same month
        from duplicating slots, and what stops a changed strategy from
        silently overwriting an existing slot's pillar/prompt: the row already
        there always wins, regardless of what the current config would now
        compute for that timestamp.

        user_id (Milestone 3.2) is optional, defaulting to None (legacy/
        unscoped) — see insert_video's docstring for why. The real
        production caller (calendar.generate_calendar.push_events) always
        passes the resolved local user's id.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO content_slots
                (scheduled_at, pillar_key, prompt, status, google_calendar_event_id, created_at, user_id)
            VALUES (?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (scheduled_at, pillar_key, prompt, google_calendar_event_id, created_at, user_id),
        )
        return cur.rowcount > 0

    def get_slot(self, slot_id: int) -> SlotRecord | None:
        row = self._conn.execute("SELECT * FROM content_slots WHERE id = ?", (slot_id,)).fetchone()
        return _row_to_slot(row) if row else None

    def find_earliest_open_slot(
        self, pillar_key: str, after_iso: str, user_id: int | None = None
    ) -> SlotRecord | None:
        """user_id (Milestone 3.2) is optional; when provided, restricts the
        match to slots owned by that user (or unowned, legacy, slots —
        `user_id = ? OR user_id IS NULL` — see docstring on the equivalent
        clause in find_earliest_open_slot_fifo below for why NULL stays
        eligible). Omitting it preserves the exact pre-3.2 unscoped query."""
        if user_id is None:
            row = self._conn.execute(
                """
                SELECT * FROM content_slots
                WHERE pillar_key = ? AND status = 'OPEN' AND scheduled_at > ?
                ORDER BY scheduled_at ASC
                LIMIT 1
                """,
                (pillar_key, after_iso),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM content_slots
                WHERE pillar_key = ? AND status = 'OPEN' AND scheduled_at > ?
                  AND (user_id = ? OR user_id IS NULL)
                ORDER BY scheduled_at ASC
                LIMIT 1
                """,
                (pillar_key, after_iso, user_id),
            ).fetchone()
        return _row_to_slot(row) if row else None

    def find_earliest_open_slot_fifo(self, after_iso: str, user_id: int | None = None) -> SlotRecord | None:
        """Earliest OPEN slot at or after after_iso, regardless of pillar_key.
        Inclusive (>=), matching the FIFO matching contract in
        docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md —
        deliberately different from find_earliest_open_slot's exclusive (>),
        which stays unchanged for pillar mode.

        user_id (Milestone 3.2) is optional; when provided, restricts the
        match to slots owned by that user, or unowned (NULL) slots —
        NULL stays eligible so a real month's worth of pre-3.2 legacy slots
        (created before ownership existed) remain assignable to the first
        scoped caller that reaches them, rather than becoming permanently
        stuck OPEN and unmatchable. Omitting user_id entirely preserves the
        exact pre-3.2 unscoped query.
        """
        if user_id is None:
            row = self._conn.execute(
                """
                SELECT * FROM content_slots
                WHERE status = 'OPEN' AND scheduled_at >= ?
                ORDER BY scheduled_at ASC
                LIMIT 1
                """,
                (after_iso,),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM content_slots
                WHERE status = 'OPEN' AND scheduled_at >= ? AND (user_id = ? OR user_id IS NULL)
                ORDER BY scheduled_at ASC
                LIMIT 1
                """,
                (after_iso, user_id),
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
        """Atomically claim a slot for a video. Raises SlotUnavailableError if
        the slot is no longer OPEN.

        Milestone 3.2 (ownership): also raises OwnershipMismatchError if both
        the video and the slot already carry a non-NULL user_id and those
        two values differ — the Phase 7 invariant
        (video.user_id == assigned_slot.user_id) enforced at the one real
        write-time choke point, rather than only trusted to callers picking
        a same-owner slot correctly. Deliberately does NOT raise when either
        side is NULL (legacy/unscoped) — every pre-3.2 call site, and every
        existing test, assigns unowned videos to unowned slots and must keep
        working unchanged.
        """
        with self.transaction() as conn:
            slot_row = conn.execute(
                "SELECT status, user_id FROM content_slots WHERE id = ?", (slot_id,)
            ).fetchone()
            if slot_row is None or slot_row["status"] != "OPEN":
                raise SlotUnavailableError(f"content_slot {slot_id} is not OPEN")

            video_row = conn.execute("SELECT user_id FROM videos WHERE id = ?", (video_id,)).fetchone()
            video_user_id = video_row["user_id"] if video_row else None
            slot_user_id = slot_row["user_id"]
            if video_user_id is not None and slot_user_id is not None and video_user_id != slot_user_id:
                raise OwnershipMismatchError(
                    f"video {video_id} (user_id={video_user_id}) cannot be assigned to "
                    f"content_slot {slot_id} (user_id={slot_user_id}) — different owners."
                )

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
        self, video_id: int, platform: str, created_at: str, scheduled_at: str | None = None,
        user_id: int | None = None,
    ) -> PlatformPostRecord:
        """Create the one publishing record for this (video, platform) pair.

        Raises sqlite3.IntegrityError (UNIQUE(video_id, platform)) if one
        already exists — callers must check get_platform_post() first and
        update the existing row instead; this is the idempotency guard
        against accidentally submitting the same post twice, enforced by
        the schema rather than only by caller discipline.

        user_id (Milestone 3.2) is optional, defaulting to None (legacy/
        unscoped) — see insert_video's docstring for why.
        """
        self._conn.execute(
            """
            INSERT INTO platform_posts (video_id, platform, status, scheduled_at, created_at, updated_at, user_id)
            VALUES (?, ?, 'PENDING', ?, ?, ?, ?)
            """,
            (video_id, platform, scheduled_at, created_at, created_at, user_id),
        )
        return self.get_platform_post(video_id, platform)

    def insert_platform_post_if_missing(
        self, video_id: int, platform: str, scheduled_at: str, created_at: str, user_id: int | None = None,
    ) -> bool:
        """Create a PENDING platform_posts row for (video_id, platform)
        unless one already exists. Returns True if a new row was created,
        False if one already existed — mirrors insert_slot_if_missing's
        contract exactly (INSERT OR IGNORE against the existing
        UNIQUE(video_id, platform) constraint).

        Milestone 2.1.2 (platform-post materialization): unlike
        insert_platform_post (which raises on a duplicate, expecting the
        caller to have already checked get_platform_post()), this is safe
        to call unconditionally and repeatedly for the same pair — it never
        runs an UPDATE, so it can never reset an existing row's
        status/platform_post_id/published_at/failure_reason, no matter how
        many times assignment/materialization logic is revisited for the
        same video.

        user_id (Milestone 3.2) is optional, defaulting to None (legacy/
        unscoped) — the real production caller
        (scheduling.platform_post_materializer.materialize_platform_posts_for_assignment)
        always passes the owning video's user_id.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO platform_posts
                (video_id, platform, status, scheduled_at, created_at, updated_at, user_id)
            VALUES (?, ?, 'PENDING', ?, ?, ?, ?)
            """,
            (video_id, platform, scheduled_at, created_at, created_at, user_id),
        )
        return cur.rowcount > 0

    def update_platform_post(self, post_id: int, updated_at: str, **fields) -> None:
        fields = {**fields, "updated_at": updated_at}
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), post_id]
        self._conn.execute(f"UPDATE platform_posts SET {columns} WHERE id = ?", values)

    def claim_platform_post(self, post_id: int, updated_at: str, user_id: int | None = None) -> bool:
        """Atomically transition platform_posts.id=post_id from PENDING to
        PUBLISHING. Returns True if this call performed the transition
        (this caller now owns the row), False if the row didn't exist or
        was no longer PENDING (already claimed by another caller, or in a
        terminal PUBLISHING/PUBLISHED/FAILED state) — never raises merely
        because another claimant won first.

        Milestone 2.1.3 (atomic platform-post claiming): a single
        UPDATE ... WHERE id = ? AND status = 'PENDING' is one indivisible
        SQLite write — two connections racing to claim the same row are
        serialized by SQLite's own file-level locking, so at most one
        UPDATE can ever see status = 'PENDING' still true; the loser's
        WHERE clause simply no longer matches (rowcount = 0). Deliberately
        not a SELECT-then-UPDATE — that would let two callers both
        observe PENDING before either writes. See
        docs/evaluations/scheduling/milestone-2.1.3-atomic-platform-post-claiming.md.

        Only status and updated_at are ever written by a claim —
        platform_post_id, scheduled_at, published_at, failure_reason,
        video_id, and platform are never touched here.

        Milestone 3.2 (ownership): user_id is optional; when provided, the
        WHERE clause also requires `user_id = ?`, so a row this caller does
        not own returns rowcount = 0 exactly like "already claimed by
        someone else" — the same "never raises, just doesn't win" contract,
        now covering a cross-tenant claim attempt too. This is
        defense-in-depth: the real selection guarantee already lives in
        get_due_platform_posts' own user_id scoping (a caller scoped to
        user A never even sees user B's row id to pass here) — this is the
        second, independent check at the actual ownership-transition point.
        """
        if user_id is None:
            cur = self._conn.execute(
                "UPDATE platform_posts SET status = 'PUBLISHING', updated_at = ? WHERE id = ? AND status = 'PENDING'",
                (updated_at, post_id),
            )
        else:
            cur = self._conn.execute(
                "UPDATE platform_posts SET status = 'PUBLISHING', updated_at = ? "
                "WHERE id = ? AND status = 'PENDING' AND user_id = ?",
                (updated_at, post_id, user_id),
            )
        return cur.rowcount > 0

    def get_due_platform_posts(
        self, platform: str, now_iso: str, eligible_statuses: list[str], user_id: int | None = None
    ) -> list[PlatformPostRecord]:
        """platform_posts rows for `platform` that are scheduled (scheduled_at
        IS NOT NULL), due (scheduled_at <= now_iso — inclusive, so a row
        scheduled exactly at now_iso is due), not waiting on a scheduled
        retry (next_retry_at IS NULL OR next_retry_at <= now_iso — same
        now_iso and same naive-local-time convention as scheduled_at, see
        Milestone 2.1.6), and in one of eligible_statuses. Ordered by the
        original scheduled_at first, ties broken by id — deliberately NOT
        by next_retry_at: scheduled_at reflects the calendar-driven posting
        order that matters to the business, and a retry's internal backoff
        timing should never reorder that relative to other due content
        (see docs/evaluations/scheduling/milestone-2.1.6-retry-backoff.md
        "Due Selection"). Pure read — never mutates a row.

        Milestone 2.1.1 (due-post detection): this is the query layer only.
        now_iso and eligible_statuses are supplied by the caller (see
        due_post_selector.get_due_posts) rather than decided here, so this
        method carries no timezone or business-eligibility logic of its
        own — same division of responsibility as find_earliest_open_slot_fifo/
        slot_matcher.py.

        Milestone 3.2 (ownership): user_id is optional; when provided,
        restricts results to that owner's rows only — this is the actual
        enforcement point behind the "a hosted background job must never
        operate on one user's records using another user's credentials"
        invariant (docs/architecture/hosted-product-boundary.md §5).
        Omitting it preserves the exact pre-3.2 unscoped query.
        """
        placeholders = ", ".join("?" for _ in eligible_statuses)
        if user_id is None:
            rows = self._conn.execute(
                f"""
                SELECT * FROM platform_posts
                WHERE platform = ?
                  AND scheduled_at IS NOT NULL
                  AND scheduled_at <= ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                  AND status IN ({placeholders})
                ORDER BY scheduled_at ASC, id ASC
                """,
                (platform, now_iso, now_iso, *eligible_statuses),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""
                SELECT * FROM platform_posts
                WHERE platform = ?
                  AND scheduled_at IS NOT NULL
                  AND scheduled_at <= ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                  AND status IN ({placeholders})
                  AND user_id = ?
                ORDER BY scheduled_at ASC, id ASC
                """,
                (platform, now_iso, now_iso, *eligible_statuses, user_id),
            ).fetchall()
        return [_row_to_platform_post(row) for row in rows]

    def get_recoverable_platform_posts(
        self, platform: str, stale_before_iso: str, user_id: int | None = None
    ) -> list[PlatformPostRecord]:
        """platform_posts rows for `platform` that are PUBLISHING and have
        not been touched since before stale_before_iso — candidates for
        crash_recovery.py, not ordinary due work (get_due_platform_posts
        stays PENDING-only). Ordered oldest-updated first, ties broken by
        id, for deterministic output. Pure read — never mutates a row.

        Milestone 2.1.5 (crash recovery): stale_before_iso must be an aware
        UTC isoformat string, matching how updated_at is always written
        (see publish_tiktok._now_iso/worker._now_iso) — this is a
        deliberately different time convention from
        get_due_platform_posts' now_iso, which is naive local time
        matching scheduled_at. Mixing the two would silently miscompare.

        user_id (Milestone 3.2) is optional; see get_due_platform_posts'
        docstring — same scoping contract.
        """
        if user_id is None:
            rows = self._conn.execute(
                "SELECT * FROM platform_posts WHERE platform = ? AND status = 'PUBLISHING' AND updated_at < ? "
                "ORDER BY updated_at ASC, id ASC",
                (platform, stale_before_iso),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM platform_posts WHERE platform = ? AND status = 'PUBLISHING' AND updated_at < ? "
                "AND user_id = ? ORDER BY updated_at ASC, id ASC",
                (platform, stale_before_iso, user_id),
            ).fetchall()
        return [_row_to_platform_post(row) for row in rows]

    def get_reconcilable_platform_posts(
        self, platform: str, now_iso: str, user_id: int | None = None
    ) -> list[PlatformPostRecord]:
        """platform_posts rows for `platform` that TikTok has already
        accepted (status = PUBLISHING AND platform_post_id IS NOT NULL) and
        are due for another status check (next_status_check_at IS NULL OR
        next_status_check_at <= now_iso) — reconciliation.py's routine
        polling candidates (Milestone 2.1.10), deliberately distinct from
        both get_due_platform_posts (PENDING-only — work never yet
        submitted) and get_recoverable_platform_posts (staleness-gated
        safety net for an abandoned/crashed claim, which does not require
        platform_post_id to be set at all — see crash_recovery.py's Case
        A/B split). A row here is never re-submitted, only re-polled — see
        publish_tiktok._resolve_poll_outcome, the one shared mapping every
        status-check caller (this module, crash_recovery.py's Case B, and
        the inline post-submission poll) applies.

        NULL next_status_check_at is immediately eligible — same
        NULL-means-no-gate convention get_due_platform_posts already uses
        for next_retry_at — covering any row that predates this milestone's
        migration and has never had a check scheduled for it yet.

        now_iso must be an aware UTC isoformat string, matching how
        next_status_check_at/updated_at are always written — the same
        convention get_recoverable_platform_posts already requires (and a
        deliberately different one from get_due_platform_posts' naive-
        local-time now_iso). Ordered oldest-due-for-a-check first, ties
        broken by id. Pure read — never mutates a row.

        user_id (Milestone 3.2) is optional; see get_due_platform_posts'
        docstring — same scoping contract.
        """
        if user_id is None:
            rows = self._conn.execute(
                "SELECT * FROM platform_posts WHERE platform = ? AND status = 'PUBLISHING' "
                "AND platform_post_id IS NOT NULL "
                "AND (next_status_check_at IS NULL OR next_status_check_at <= ?) "
                "ORDER BY next_status_check_at ASC, id ASC",
                (platform, now_iso),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM platform_posts WHERE platform = ? AND status = 'PUBLISHING' "
                "AND platform_post_id IS NOT NULL "
                "AND (next_status_check_at IS NULL OR next_status_check_at <= ?) "
                "AND user_id = ? "
                "ORDER BY next_status_check_at ASC, id ASC",
                (platform, now_iso, user_id),
            ).fetchall()
        return [_row_to_platform_post(row) for row in rows]

    def update_platform_post_if_unchanged(
        self, post_id: int, expected_updated_at: str, updated_at: str, user_id: int | None = None, **fields
    ) -> bool:
        """Optimistic-concurrency update: apply fields (plus updated_at)
        only if the row's updated_at still equals expected_updated_at —
        i.e. only if nothing has touched it since the caller last read it.
        Returns True if this call performed the update, False if the row
        had already changed (or didn't exist) — never raises merely
        because it lost a race.

        Milestone 2.1.5 (crash recovery): every write in this codebase
        that mutates a platform_posts row also bumps updated_at (claim,
        submission, poll outcomes, materialization is insert-only) — so
        updated_at already serves as a de facto version/CAS token with no
        new column needed. Used by crash_recovery.py so it can act only on
        a row that is still the exact stale record it inspected, never on
        one an active worker resumed and already moved on.

        Milestone 3.2 (ownership): user_id is optional (keyword-only in
        practice — always pass fields by keyword, as every existing caller
        already does); when provided, the WHERE clause also requires
        `user_id = ?`, the same defense-in-depth pattern as
        claim_platform_post. A caller scoped to the wrong user simply loses
        the compare-and-swap, exactly like any other lost race.
        """
        fields = {**fields, "updated_at": updated_at}
        columns = ", ".join(f"{key} = ?" for key in fields)
        if user_id is None:
            values = [*fields.values(), post_id, expected_updated_at]
            cur = self._conn.execute(
                f"UPDATE platform_posts SET {columns} WHERE id = ? AND updated_at = ?", values
            )
        else:
            values = [*fields.values(), post_id, expected_updated_at, user_id]
            cur = self._conn.execute(
                f"UPDATE platform_posts SET {columns} WHERE id = ? AND updated_at = ? AND user_id = ?", values
            )
        return cur.rowcount > 0

    # -- users / auth_identities / platform_connections (Milestone 3.2) -

    def create_user(self, email: str, display_name: str | None, created_at: str) -> UserRecord:
        """Raises sqlite3.IntegrityError if email already exists (UNIQUE) —
        callers that want get-or-create semantics should use
        get_or_create_local_user() or check get_user_by_email() first."""
        self._conn.execute(
            "INSERT INTO users (email, display_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (email, display_name, created_at, created_at),
        )
        return self.get_user_by_email(email)

    def get_user(self, user_id: int) -> UserRecord | None:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> UserRecord | None:
        row = self._conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return _row_to_user(row) if row else None

    def get_or_create_local_user(self) -> UserRecord:
        """Resolve the single local bootstrap user (LOCAL_BOOTSTRAP_USER_EMAIL),
        creating it on first call. This is the transitional bridge every CLI
        entry point uses until a real authenticated multi-user API exists
        (Milestone 3.2's Phase 9/16) — analogous to
        calendar_manager.resolve_app_calendar's "reuse via persisted state,
        create if missing" pattern, applied to the local user identity
        instead of the dedicated Calendar. Idempotent: calling this
        repeatedly against the same database always returns the same user,
        never creates a second one (get_user_by_email is checked first)."""
        existing = self.get_user_by_email(LOCAL_BOOTSTRAP_USER_EMAIL)
        if existing is not None:
            return existing
        now = _utc_now_iso()
        return self.create_user(LOCAL_BOOTSTRAP_USER_EMAIL, "Local Bootstrap User", now)

    def create_auth_identity(
        self, user_id: int, provider: str, provider_subject: str, provider_email: str | None, created_at: str,
    ) -> AuthIdentityRecord:
        """Raises sqlite3.IntegrityError if (provider, provider_subject) is
        already linked to a user (UNIQUE) — a given external identity can
        never map to two different Pickle Batch accounts."""
        cur = self._conn.execute(
            "INSERT INTO auth_identities (user_id, provider, provider_subject, provider_email, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, provider, provider_subject, provider_email, created_at, created_at),
        )
        row = self._conn.execute("SELECT * FROM auth_identities WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_auth_identity(row)

    def get_user_by_auth_identity(self, provider: str, provider_subject: str) -> UserRecord | None:
        row = self._conn.execute(
            "SELECT users.* FROM users JOIN auth_identities ON auth_identities.user_id = users.id "
            "WHERE auth_identities.provider = ? AND auth_identities.provider_subject = ?",
            (provider, provider_subject),
        ).fetchone()
        return _row_to_user(row) if row else None

    def create_platform_connection(
        self, user_id: int, platform: str, external_account_id: str | None, status: str, created_at: str,
    ) -> PlatformConnectionRecord:
        """Raises sqlite3.IntegrityError if (user_id, platform) already has a
        connection (UNIQUE — one connection per platform per user for V1,
        see SCHEMA_PLATFORM_CONNECTIONS). Callers that want get-or-create
        semantics should use get_or_create_platform_connection()."""
        cur = self._conn.execute(
            "INSERT INTO platform_connections (user_id, platform, external_account_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, platform, external_account_id, status, created_at, created_at),
        )
        row = self._conn.execute("SELECT * FROM platform_connections WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_platform_connection(row)

    def get_platform_connection(self, user_id: int, platform: str) -> PlatformConnectionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM platform_connections WHERE user_id = ? AND platform = ?", (user_id, platform)
        ).fetchone()
        return _row_to_platform_connection(row) if row else None

    def get_or_create_platform_connection(
        self, user_id: int, platform: str, external_account_id: str | None = None,
    ) -> PlatformConnectionRecord:
        """Resolve the (user_id, platform) connection, creating it (status
        ACTIVE) on first call. Idempotent like get_or_create_local_user().
        external_account_id is only applied on creation — an existing
        connection's external_account_id is never overwritten by a later
        get_or_create call, matching insert_slot_if_missing/
        insert_platform_post_if_missing's "the row already there always
        wins" convention elsewhere in this store."""
        existing = self.get_platform_connection(user_id, platform)
        if existing is not None:
            return existing
        now = _utc_now_iso()
        return self.create_platform_connection(user_id, platform, external_account_id, "ACTIVE", now)

    def update_platform_connection_status(self, connection_id: int, status: str, updated_at: str) -> None:
        """Used by the hosted OAuth connect/disconnect flow (Milestone
        3.6) — e.g. DISCONNECTED on disconnect, back to ACTIVE on a
        reconnect. Unconditional (no CAS) — status transitions here are
        always driven by one explicit, authenticated user action at a
        time, not a background refresh race like platform_credentials'
        update_platform_credential_if_unchanged."""
        self._conn.execute(
            "UPDATE platform_connections SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, connection_id),
        )

    # -- platform_credentials / oauth_states (Milestone 3.6) -------------

    def get_platform_credential(self, platform_connection_id: int) -> PlatformCredentialRecord | None:
        row = self._conn.execute(
            "SELECT * FROM platform_credentials WHERE platform_connection_id = ?", (platform_connection_id,)
        ).fetchone()
        return _row_to_platform_credential(row) if row else None

    def upsert_platform_credential(
        self, platform_connection_id: int, encrypted_payload: str, now: str,
    ) -> PlatformCredentialRecord:
        """Create-or-unconditionally-overwrite a connection's stored
        credential. Used by the OAuth connect flow (a fresh, explicit
        user-initiated authorization always wins, exactly like
        tiktok_auth.save_token()'s own unconditional overwrite) — never
        used by the refresh path, which must use
        update_platform_credential_if_unchanged instead so a concurrent
        refresh can't silently clobber a newer one."""
        existing = self.get_platform_credential(platform_connection_id)
        if existing is None:
            self._conn.execute(
                "INSERT INTO platform_credentials (platform_connection_id, encrypted_payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (platform_connection_id, encrypted_payload, now, now),
            )
        else:
            self._conn.execute(
                "UPDATE platform_credentials SET encrypted_payload = ?, updated_at = ? WHERE platform_connection_id = ?",
                (encrypted_payload, now, platform_connection_id),
            )
        return self.get_platform_credential(platform_connection_id)

    def update_platform_credential_if_unchanged(
        self, platform_connection_id: int, encrypted_payload: str, expected_updated_at: str, new_updated_at: str,
    ) -> bool:
        """Optimistic-concurrency (CAS) update for the refresh path — the
        same update_platform_post_if_unchanged pattern (compare-and-swap on
        updated_at) used elsewhere in this store, so two concurrent hosted
        requests refreshing the same expired TikTok credential can't both
        win: the second writer's expected_updated_at is stale by the time it
        writes, its update affects zero rows, and it re-reads instead of
        overwriting an already-refreshed (and possibly already-rotated-away)
        token. Returns True if this call's write won."""
        cur = self._conn.execute(
            "UPDATE platform_credentials SET encrypted_payload = ?, updated_at = ? "
            "WHERE platform_connection_id = ? AND updated_at = ?",
            (encrypted_payload, new_updated_at, platform_connection_id, expected_updated_at),
        )
        return cur.rowcount > 0

    def delete_platform_credential(self, platform_connection_id: int) -> None:
        """Used by disconnect (Phase 20) — removes only the credential
        secret, never the platform_connections row itself (which retains
        identity/status history; its status is set to DISCONNECTED by the
        caller, not deleted)."""
        self._conn.execute(
            "DELETE FROM platform_credentials WHERE platform_connection_id = ?", (platform_connection_id,)
        )

    def create_oauth_state(
        self, user_id: int, platform: str, state: str, code_verifier: str, redirect_uri: str,
        created_at: str, expires_at: str,
    ) -> OAuthStateRecord:
        """Raises sqlite3.IntegrityError on a state collision (UNIQUE) —
        astronomically unlikely (state is secrets.token_urlsafe-generated)
        but fails loudly rather than silently reusing another attempt's
        row."""
        cur = self._conn.execute(
            "INSERT INTO oauth_states (user_id, platform, state, code_verifier, redirect_uri, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, platform, state, code_verifier, redirect_uri, created_at, expires_at),
        )
        row = self._conn.execute("SELECT * FROM oauth_states WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_oauth_state(row)

    def consume_oauth_state(self, state: str, now: str) -> OAuthStateRecord | None:
        """Atomically marks a pending OAuth state consumed and returns the
        row that was consumed — or None if `state` doesn't exist, was
        already consumed (replay), or is past expires_at. The read (to
        return the row's user_id/code_verifier/redirect_uri/platform to the
        caller) happens before the CAS write, but the write's own
        `WHERE consumed_at IS NULL` clause is what actually prevents two
        concurrent callback requests presenting the same state from both
        succeeding — only one UPDATE can win; the loser gets rowcount=0 and
        this returns None to it, exactly like
        update_platform_credential_if_unchanged's race handling above."""
        row = self._conn.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
        if row is None:
            return None
        record = _row_to_oauth_state(row)
        if record.consumed_at is not None:
            return None
        if now >= record.expires_at:
            return None
        cur = self._conn.execute(
            "UPDATE oauth_states SET consumed_at = ? WHERE state = ? AND consumed_at IS NULL", (now, state)
        )
        if cur.rowcount == 0:
            return None
        record.consumed_at = now
        return record


class SlotUnavailableError(Exception):
    """Raised when a slot is claimed between selection and assignment."""


class OwnershipMismatchError(Exception):
    """Raised by assign_slot() (Milestone 3.2) when a video and the
    content_slot it's being assigned to are both explicitly owned by
    different users. See assign_slot's docstring."""


def _raise_missing(lastrowid: int):
    raise RuntimeError(f"insert succeeded but row {lastrowid} could not be re-read")
