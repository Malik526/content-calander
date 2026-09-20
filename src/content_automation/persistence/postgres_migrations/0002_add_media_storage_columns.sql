-- 0002_add_media_storage_columns.sql — Milestone 3.4 (object storage /
-- media lifecycle). This repository's first real forward schema evolution
-- against an already-applied Postgres baseline (0001_initial_schema.sql,
-- Milestone 3.3) — deliberately a new file, not a rewrite of 0001, per
-- this milestone's own instruction and the migration mechanism's whole
-- purpose (postgres_migrate.py tracks applied filenames; 0001 is already
-- recorded as applied against the real "public" schema).
--
-- Both columns nullable, matching SQLite's identical additive migration
-- (persistence/content_store.py's _VIDEOS_MIGRATION_COLUMNS) — unlike
-- Milestone 3.3's user_id decision (made NOT NULL because every real row
-- was already ownership-complete at import time), no real video row has a
-- storage_provider/storage_key value yet, so there is nothing to backfill
-- before a NOT NULL constraint could apply, and every existing real video
-- must keep working exactly as today (canonical_media_path still the
-- authoritative local path) until migrated. See
-- docs/decisions/0009-object-storage-media-lifecycle.md "Canonical Media
-- Reference".

ALTER TABLE videos ADD COLUMN storage_provider TEXT;
ALTER TABLE videos ADD COLUMN storage_key TEXT;
