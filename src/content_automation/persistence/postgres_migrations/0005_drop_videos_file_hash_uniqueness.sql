-- 0005_drop_videos_file_hash_uniqueness.sql — Milestone 3.7 re-upload
-- architecture follow-up.
--
-- videos.file_hash was globally UNIQUE from 0001_initial_schema.sql
-- onward, which conflated two different things: videos.id is the real
-- unique identity of one upload/record; file_hash is a reusable content
-- fingerprint a creator may legitimately attach to more than one record
-- over time (the same finished video re-uploaded later with a new
-- caption/schedule/campaign/performance history). Dropping the
-- constraint and replacing it with a plain index, so hash-based
-- duplicate-detection/analytics lookups (get_video_by_hash, a future
-- "flag likely duplicates" query) stay fast without the uniqueness that
-- was blocking legitimate re-uploads. See
-- docs/decisions/0009-object-storage-media-lifecycle.md's follow-up
-- addendum and persistence/content_store.py's matching SQLite migration
-- (_migrate_videos_drop_file_hash_uniqueness).
--
-- videos_file_hash_key is the name Postgres auto-generated for the
-- inline `file_hash TEXT NOT NULL UNIQUE` column constraint in
-- 0001_initial_schema.sql ("<table>_<column>_key", Postgres's own default
-- naming convention) — never renamed since, so it's safe to name
-- explicitly here rather than looking it up dynamically.

ALTER TABLE videos DROP CONSTRAINT IF EXISTS videos_file_hash_key;

CREATE INDEX IF NOT EXISTS idx_videos_file_hash ON videos (file_hash);
