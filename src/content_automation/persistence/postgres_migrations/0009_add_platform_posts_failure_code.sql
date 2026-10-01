-- 0009_add_platform_posts_failure_code.sql — Milestone 3.11 (user-facing
-- publish states + errors). Purely additive: one nullable column.
--
-- platform_posts.failure_code is the structured reason_code behind the
-- existing free-text failure_reason (PublishError.reason_code, a local
-- precondition code, or the platform's own reported fail code). The
-- user-facing failure taxonomy (publishing/failure_taxonomy.py) keys off
-- this column; failure_reason stays internal diagnostic text and is never
-- returned by the API. NULL for every row written before this migration —
-- those rows map to the generic "unknown" category rather than being
-- inferred from failure_reason's text. Same column as
-- persistence/content_store.py's SQLite _PLATFORM_POSTS_MIGRATION_COLUMNS.

ALTER TABLE platform_posts ADD COLUMN IF NOT EXISTS failure_code TEXT;
