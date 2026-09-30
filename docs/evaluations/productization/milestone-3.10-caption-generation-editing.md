# Milestone 3.10 — Caption Generation + Editing

## Objective

Give each uploaded/scheduled video an editable publishing caption that persists and that the
TikTok publishing path consumes. The work covers caption persistence, the generation boundary,
editing, and publishing handoff. Milestone 5 content intelligence is excluded. Architecture
decisions are recorded in `docs/decisions/0014-canonical-caption-ownership-and-provenance.md`.

## Existing Architecture Found

- `videos.caption_text`/`caption_source` already existed on both backends (Milestone 1.3) as
  the canonical, video-level caption, written only by `media/processing.py` (CLI ingestion).
  No CHECK constraint on `caption_source`.
- `platform_posts` has no caption column. The TikTok path read `video.caption_text` directly
  in `scheduling/publish_tiktok.py` (validation plus `publisher.publish`), re-reading the
  video at execution time.
- Transcripts exist only for CLI-ingested videos. Hosted uploads are never transcribed.
- `file_hash` has not been unique since the 3.7 re-upload change, so per-record captions
  needed no work.

No mismatch required a product decision. The safe solution (reuse the existing columns,
add a resolver seam) was clear.

## What Was Built

**Backend**
- `media/caption_generation.py`: the generation contract (`CaptionGenerationRequest` →
  `GeneratedCaption`, `generate_caption()`, `can_generate_caption()`), backed only by the
  existing `transcript_auto` derivation.
- `media/caption_editing.py`: `save_caption`, `regenerate_caption(overwrite=)`,
  `caption_provenance`, `is_caption_locked`, and the `_edited` provenance rule.
- `publishing/caption_resolution.py`: `resolve_publish_caption(video, platform)`, now used by
  both caption reads in `scheduling/publish_tiktok.py` (behavior unchanged).
- API (`api/routes/captions.py`, `api/schemas/captions.py`):
  - `GET /api/videos/{id}/caption`
  - `PUT /api/videos/{id}/caption` (`{caption_text}`, max `config.CAPTION_TEXT_MAX_CHARS` = 10000)
  - `POST /api/videos/{id}/caption/generate` (`{overwrite}`)
  - Response: `{video_id, caption_text, provenance, can_generate, editable}`. No transcript
    or raw `caption_source` is exposed.
  - Cross-user access returns 404. Locked, overwrite-required, and generation-unavailable
    cases return 409.
- `GET /api/queue/slots`: `assigned_video` now carries `caption` (the same shape).
- No schema migration.

**Frontend**
- `components/app/CaptionEditor.tsx`: textarea, provenance label, Save,
  Generate/Regenerate (only when `can_generate`), and an inline "Replace caption" confirm
  whenever saved or unsaved text would be lost. Read-only once the caption is locked.
- `QueueSlotCard` renders it for any assigned slot (list rows and the calendar detail panel).
  `QueueBoard` patches the affected slot in place on save, so other slots' drafts survive.
- `lib/api/captions.ts`, plus the `CaptionResponse` type.

## Verification

- Backend: `.venv/bin/python3 -m pytest` → **922 passed** (baseline 881; +41). This includes
  the Postgres-backed suite against `config.POSTGRES_TEST_SCHEMA`, since `DATABASE_URL` was
  configured in this environment.
  - `tests/test_caption_editing.py` (25): generation contract, provenance table,
    manual/generated/edited transitions, clearing, identical-hash independence, explicit
    regeneration, no-transcript, and locking (PENDING editable;
    PUBLISHING/PUBLISHED/FAILED-with-id locked).
  - `tests/test_api_captions.py` (12): auth 401, cross-user 404 on all three routes with no
    mutation, a persisted value visible on refetch, no transcript leak, length 422,
    identical-hash independence, generate 409 without a transcript, generate-then-edit →
    `GENERATED_EDITED`, overwrite required, lock 409, and the assigned Queue item exposing
    its caption.
  - `tests/test_publish_tiktok.py` (+3): a caption edited after the PENDING row exists is
    the one sent; each identical-hash record publishes its own caption; the resolver returns
    the canonical caption.
  - `tests/test_postgres_content_store.py` (+1): identical-hash independence against real Postgres.
  - Existing queue/scheduling/publish tests are all unchanged and passing
    (`test_api_queue.py` included).
- Frontend (`web/`): `npm run test` → **115 passed** (baseline 107; +8:
  `tests/components/CaptionEditor.test.tsx` 5, `tests/routes/queue-board.test.tsx` +3).
  `npm run lint` clean. `npm run build` clean with placeholder Supabase env values.
- Not done: no live deployed-stack check (Railway/Netlify) and no real TikTok call. The API
  was exercised through FastAPI's `TestClient` against the real app object, not a running server.

## Deferred (Milestone 5+)

Per-platform caption overrides (seam in place), smarter/LLM generation, creator-style
learning, embeddings, content-pillar intelligence, performance-based recommendations,
multi-platform caption optimization, hosted transcription, caption version history/approval
states, and hosted scheduler/workers.
