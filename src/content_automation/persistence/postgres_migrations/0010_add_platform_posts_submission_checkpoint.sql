-- 0010_add_platform_posts_submission_checkpoint.sql — Milestone 3.13
-- (reconciliation + recovery). Purely additive: two nullable columns.
--
-- submission_state is a checkpoint written immediately before a publisher
-- is called and cleared once the outcome is known, so crash recovery can
-- tell "claimed but never submitted" (NULL) from "submission started"
-- (AWAITING_PLATFORM_ID — the publisher persists its platform id before
-- transferring any media, so no id means no media was sent; SUBMITTING —
-- outcome unknowable, parked as status UNKNOWN for manual recovery).
-- submission_started_at is diagnostic (aware UTC). NULL for every existing
-- row. Same columns as persistence/content_store.py's SQLite
-- _PLATFORM_POSTS_MIGRATION_COLUMNS.
--
-- platform_posts.status gains one value, UNKNOWN (no constraint lists the
-- allowed values, so no DDL is needed for it): an outcome that could not be
-- determined from evidence, never selected by due selection, crash recovery
-- or reconciliation, and released only by the manual retry API.

ALTER TABLE platform_posts ADD COLUMN IF NOT EXISTS submission_state TEXT;
ALTER TABLE platform_posts ADD COLUMN IF NOT EXISTS submission_started_at TIMESTAMPTZ;
