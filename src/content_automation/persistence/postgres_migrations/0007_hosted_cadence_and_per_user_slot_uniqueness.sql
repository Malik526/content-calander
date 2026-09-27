-- 0007_hosted_cadence_and_per_user_slot_uniqueness.sql — Milestone 3.8
-- (hosted scheduling cadence configuration + future slot generation).
--
-- Two independent changes:
--
-- 1. content_slots.scheduled_at had a GLOBAL UNIQUE constraint, not
--    per-user (content_slots_scheduled_at_key, from 0001_initial_schema.sql
--    -- confirmed against the real constraint name in production before
--    writing this migration). Two different hosted users generating a
--    slot for the same wall-clock timestamp would silently collide --
--    ON CONFLICT (scheduled_at) DO NOTHING means the second user's slot
--    simply never gets created, no error. Widened to
--    UNIQUE(user_id, scheduled_at) -- a strict widening, never a
--    narrowing: every row that satisfied the old constraint trivially
--    satisfies this one too, so no existing data can violate it. (Unlike
--    the SQLite side, Postgres's content_slots.user_id is already NOT
--    NULL -- see ADR-0007/0008 -- so there is no NULL-distinctness
--    caveat to note here the way content_store.py's equivalent SQLite
--    migration has to.)
--
-- 2. Two new tables (posting_cadences, posting_cadence_times) and two new
--    nullable columns on content_slots (timezone, cadence_id) -- see
--    docs/decisions/0012-hosted-cadence-configuration.md for the full
--    design, and persistence/content_store.py's matching SQLite schema
--    for the identical shape on that backend.

ALTER TABLE content_slots DROP CONSTRAINT content_slots_scheduled_at_key;

CREATE TABLE IF NOT EXISTS posting_cadences (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE REFERENCES users(id),
    timezone TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS posting_cadence_times (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cadence_id BIGINT NOT NULL REFERENCES posting_cadences(id),
    weekday TEXT NOT NULL,
    posting_time TEXT NOT NULL,
    UNIQUE(cadence_id, weekday, posting_time)
);

ALTER TABLE content_slots ADD COLUMN timezone TEXT;
ALTER TABLE content_slots ADD COLUMN cadence_id BIGINT REFERENCES posting_cadences(id);

ALTER TABLE content_slots ADD CONSTRAINT content_slots_user_scheduled_at_key UNIQUE(user_id, scheduled_at);
