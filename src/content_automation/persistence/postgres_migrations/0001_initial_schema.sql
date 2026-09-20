-- 0001_initial_schema.sql — Milestone 3.3 (Postgres persistence migration).
--
-- A fresh, final-shape Postgres schema for the six tables Milestone 3.2
-- (ownership) and earlier milestones established under SQLite — NOT a
-- replay of SQLite's own historical migration history. SQLite needed
-- separate ALTER TABLE / rebuild-based migrations over time
-- (content_store.py's _migrate_content_slots_unique_constraint,
-- _repair_videos_assigned_slot_fk, _ensure_*_columns) because it cannot
-- alter table constraints or FK targets in place; Postgres can express the
-- current, correct shape directly in one CREATE TABLE per table, so none of
-- that SQLite-specific migration history is replayed here — see
-- docs/decisions/0008-postgres-persistence-migration.md "Migration
-- Framework".
--
-- Ownership (Milestone 3.2 under SQLite) is NOT NULL here, not nullable —
-- SQLite could not add a NOT NULL foreign-key column to an already-populated
-- table without a full rebuild, so Milestone 3.2 left videos/content_slots/
-- platform_posts.user_id nullable and enforced ownership at the application
-- layer instead. Postgres starts from an empty schema (the real data is
-- imported afterward, already ownership-backfilled — see
-- cli/migrate_sqlite_to_postgres.py), so there is no such constraint here:
-- every creator-owned table's user_id is NOT NULL from the start. See
-- docs/decisions/0008-postgres-persistence-migration.md "Ownership".
--
-- Timestamp types (Phase 10 decision, preserving existing semantics exactly
-- — see the ADR's timestamp table): scheduled_at/next_retry_at are
-- TIMESTAMP (no time zone) because they are naive local time
-- (config.TIMEZONE), matching how this codebase already computes and
-- compares them (slot_matcher.now_in_config_timezone). created_at/
-- updated_at/published_at/next_status_check_at are TIMESTAMPTZ because they
-- are always written as aware UTC (see publish_tiktok._now_iso/
-- worker._now_iso). Nothing is normalized to a single convention here —
-- doing so would be a real behavioral change this migration explicitly does
-- not make.
--
-- content_slots.assigned_video_id and videos.assigned_slot_id reference each
-- other — content_slots is created first without that FK constraint
-- (assigned_video_id is a plain column initially), videos is created
-- second with its FK to content_slots (a valid forward reference at this
-- point), and the content_slots -> videos FK is added last via ALTER TABLE,
-- breaking the circular dependency Postgres enforces at CREATE TABLE time
-- (unlike SQLite, which never validates a REFERENCES target exists).

CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_identities (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    provider_subject TEXT NOT NULL,
    provider_email TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(provider, provider_subject)
);

CREATE TABLE IF NOT EXISTS platform_connections (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    external_account_id TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(user_id, platform)
);

CREATE TABLE IF NOT EXISTS content_slots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    scheduled_at TIMESTAMP NOT NULL UNIQUE,
    pillar_key TEXT,
    prompt TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    assigned_video_id BIGINT,
    google_calendar_event_id TEXT,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
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
    file_size_bytes BIGINT,
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
    assigned_slot_id BIGINT REFERENCES content_slots(id),
    created_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    caption_text TEXT,
    caption_source TEXT
);

ALTER TABLE content_slots
    ADD CONSTRAINT content_slots_assigned_video_id_fkey
    FOREIGN KEY (assigned_video_id) REFERENCES videos(id);

CREATE TABLE IF NOT EXISTS platform_posts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    video_id BIGINT NOT NULL REFERENCES videos(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    platform_post_id TEXT,
    scheduled_at TIMESTAMP,
    published_at TIMESTAMPTZ,
    failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,
    next_status_check_at TIMESTAMPTZ,
    status_check_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(video_id, platform)
);

-- Indexes supporting the scoped selector queries every background job runs
-- (get_due_platform_posts/get_recoverable_platform_posts/
-- get_reconcilable_platform_posts, all filtered by user_id + platform +
-- status at minimum).
CREATE INDEX IF NOT EXISTS idx_platform_posts_user_platform_status
    ON platform_posts(user_id, platform, status);
CREATE INDEX IF NOT EXISTS idx_content_slots_user_status
    ON content_slots(user_id, status);
CREATE INDEX IF NOT EXISTS idx_videos_user_id
    ON videos(user_id);
