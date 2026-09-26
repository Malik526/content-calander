-- 0004_add_upload_telemetry.sql — Milestone 3.7 follow-up (upload
-- performance instrumentation). A new forward migration, not a rewrite of
-- 0001/0002/0003 (all already applied against the real "public" schema).
--
-- Event/attempt-level, deliberately not columns on `videos` — a video row
-- describes durable content; a batch/attempt describes one transient
-- upload *event*. upload_attempts.video_id is nullable because a failed
-- attempt (unsupported file type, storage error, duplicate content) never
-- gets a video row at all. Both user_id columns are NOT NULL (this
-- codebase's Postgres-starts-from-empty-schema ownership convention — see
-- 0001's own note — no legacy/unowned-row case exists here at all, unlike
-- SQLite's nullable videos.user_id).
--
-- duration_ms on both tables is server-side wall-clock only — see
-- api/routes/videos.py's own docstring for exactly what span each duration
-- covers and why it is never labeled "network latency".

CREATE TABLE IF NOT EXISTS upload_batches (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    file_count INTEGER NOT NULL,
    total_bytes BIGINT NOT NULL DEFAULT 0,
    total_duration_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS'
);

CREATE TABLE IF NOT EXISTS upload_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES upload_batches(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    video_id BIGINT REFERENCES videos(id),
    original_filename TEXT NOT NULL,
    file_size_bytes BIGINT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    error_code TEXT
);
