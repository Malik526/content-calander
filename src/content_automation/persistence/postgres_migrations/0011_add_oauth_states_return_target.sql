-- 0011_add_oauth_states_return_target.sql — Milestone 4.1 (Instagram OAuth).
-- Purely additive: one nullable column.
--
-- return_target is where the OAuth callback sends the browser once the
-- attempt finishes, chosen at connect time from a server-owned allowlist
-- (api/oauth_return_targets.py — default the web Settings URL; later a
-- native app scheme or universal link). Only exact allowlisted values are
-- ever written. NULL for every existing row and for TikTok, which keeps its
-- fixed Settings redirect until it moves onto this column. Same column as
-- persistence/content_store.py's SQLite _OAUTH_STATES_MIGRATION_COLUMNS.
-- See docs/decisions/0018-instagram-integration-architecture.md Decision 6.

ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS return_target TEXT;
