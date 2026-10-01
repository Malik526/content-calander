-- 0008_add_video_hashtags.sql — Milestone 3.10.1 (caption hashtag parsing +
-- structured metadata). Purely additive: one new table, no change to any
-- existing table or row.
--
-- One row per hashtag occurrence in a video's current caption_text, in
-- caption order (media.hashtags.extract_hashtags). Derived metadata only —
-- videos.caption_text remains the exact publishing source of truth. Written
-- exclusively by PostgresContentStore.set_video_caption(), atomically with
-- caption_text. Same shape as persistence/content_store.py's SQLite
-- SCHEMA_VIDEO_HASHTAGS. Videos captioned before this migration have no
-- rows until their caption is next written (no backfill here).

CREATE TABLE IF NOT EXISTS video_hashtags (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id BIGINT NOT NULL REFERENCES videos(id),
    position INTEGER NOT NULL,
    hashtag TEXT NOT NULL,
    UNIQUE(video_id, position)
);
