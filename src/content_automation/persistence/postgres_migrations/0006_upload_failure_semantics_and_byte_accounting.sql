-- 0006_upload_failure_semantics_and_byte_accounting.sql — Milestone 3.7
-- upload-failure-semantics + telemetry-accuracy follow-up (2026-09-27),
-- triggered by a real Supabase 413 EntityTooLarge during hosted-upload
-- testing (see CHANGELOG.md's dated entry for the full investigation).
--
-- total_bytes silently only ever summed *successful* files' sizes in this
-- codebase's own route code (a bug, not documented intent) — misleading
-- for benchmark analysis of any batch containing a failure. Renamed to
-- attempted_bytes (every file's measured size, success or fail); a new
-- successful_bytes column carries the previously-intended
-- successful-only meaning explicitly, so neither number needs to be
-- inferred/derived from upload_attempts by a future reader.
--
-- success_count/failure_count make a batch's outcome explicit without
-- overloading `status` (which stays a pure lifecycle field —
-- IN_PROGRESS -> COMPLETED — never a result field) with an invented value
-- like PARTIAL_SUCCESS.
--
-- No videos/upload_attempts schema change here: a storage-layer upload
-- failure marking a video row FAILED (see media/media_storage.py's
-- create_video_from_upload) and an upload_attempts row retaining its true
-- attempted file_size_bytes on failure are both application-code fixes
-- against columns that already existed (videos.status/failure_reason/
-- processed_at, upload_attempts.file_size_bytes) — nothing new to add.

ALTER TABLE upload_batches RENAME COLUMN total_bytes TO attempted_bytes;
ALTER TABLE upload_batches ADD COLUMN successful_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE upload_batches ADD COLUMN success_count INTEGER;
ALTER TABLE upload_batches ADD COLUMN failure_count INTEGER;
