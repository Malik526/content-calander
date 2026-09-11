# Content Calendar — Changelog

## 2026-09-11

### Configurable Posting Cadence & Weighted Pillar Allocation — Milestone 1.1

Replaced the fixed `WEEKLY_SCHEDULE` (weekday name -> pillar key) and its `FIFTH_SUNDAY_CONTENT_TYPE` rebalancing special case with a general scheduling strategy: a configurable posts-per-week cadence, an "auto" or explicit posting-day selection, a configurable posting time, and per-pillar percentage weights for any number of pillars.

- Added `scheduling.py`: `generate_posting_dates` (posts/week + posting days + posting time -> real calendar dates, deterministic evenly-spaced auto-day selection, no LLM), `allocate_pillars` (largest-remainder method, exact integer counts, config-order tie-break), `distribute_pillars` (smooth weighted round-robin so pillars interleave instead of clustering), `validate_schedule_config` (clear rejection of invalid cadence/day/time/weight configuration).
- `generate_calendar.build_schedule(year, month)` now composes `scheduling.py` instead of the fixed weekday mapping; `ScheduledPost` carries a full `scheduled_at: datetime` and an optional `prompt`. CLI (`--month`/`--year`/`--calendar`/`--dry-run`), Google Calendar push, and prompt rotation behavior are otherwise unchanged.
- `config.py`: `CONTENT_TYPES[*]["target_percent"]` renamed to `"weight"`; added `POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`, `PROMPT_GENERATION_ENABLED`. Removed `WEEKLY_SCHEDULE` and `FIFTH_SUNDAY_CONTENT_TYPE`.
- `content_store.py`: `content_slots` uniqueness changed from `(scheduled_at, pillar_key)` to `scheduled_at` alone — one posting datetime is one slot regardless of which pillar a strategy assigns it — and `prompt` is now nullable. A database created under the old constraint is migrated automatically and non-destructively the first time it's opened (`_migrate_content_slots_unique_constraint`), keeping the most-advanced-status row for any timestamp that had duplicates under the old constraint. Regenerating a month after a strategy change remains additive-only: existing slots are never overwritten, only newly-covered dates get new ones.
- Video ingestion, transcription, classification, and `slot_matcher.py`'s routing policy are unchanged — `process_content.py` required no code changes.
- Added `tests/test_scheduling.py` (posting-date generation across leap/non-leap/30/31-day months, 1-7 posts/week, auto/explicit days, largest-remainder allocation incl. the spec's worked example, pillar sequencing, all documented validation-error cases) and `docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md`. Updated `tests/test_generate_calendar.py` and `tests/test_content_store.py` (new migration test) for the new model.
- Updated `README.md` and `PROJECT_STATE.md`.

Validation: `python3 -m pytest` (94 passed). Manually confirmed `generate_calendar.py --dry-run` still works for the default 7-posts/week config, and reproduced the brief's worked example exactly (3 posts/week, Mon/Wed/Fri, September 2026 -> 13 slots: 6 building / 4 acquisition / 3 mindset, interleaved rather than clustered).

### Video Ingestion Pipeline — Milestone 1

Added the first automated content-processing workflow on top of the existing calendar generator: drop `.mov`/`.mp4` files into `content/incoming/`, run `python3 process_content.py`, and have the system inspect, transcribe, classify, and route each video to the earliest matching future posting slot without manual assignment.

- Added `content_store.py` (SQLite `videos` + `content_slots` tables), `media.py` (ffprobe inspection, TikTok-compatibility check, audio extraction), `transcription.py` (`Transcriber` interface, `FasterWhisperTranscriber`), `classification.py` (`ContentClassifier` interface, `ClaudeClassifier` via forced tool-use), `slot_matcher.py` (deterministic earliest-open-slot selection), and `process_content.py` (thin orchestrator + CLI).
- Extended `generate_calendar.py` to persist a `content_slots` row per generated event alongside the existing Google Calendar push, idempotently on `(scheduled_at, pillar_key)` so re-running a month does not duplicate slots. Existing CLI, allocation logic, weekly mapping, fifth-Sunday rebalancing, prompt rotation, and dry-run behavior are unchanged.
- Extended `config.py` with pillar descriptions (for the classifier), `AUTO_ASSIGN_THRESHOLD`, transcription/classification vendor settings, file lifecycle directories, and TikTok generic compatibility targets.
- Added `pytest` suite (`tests/`) covering media inspection, scheduling, classification-policy validation, content_store idempotency, existing calendar-generation behavior, and a full-pipeline integration test against a real ffmpeg-synthesized video.
- Added `docs/decisions/0001-video-ingestion-pipeline.md` (architecture decision) and `PROJECT_STATE.md` (current-state snapshot), following the pattern already established in `internal-tools/service-business-prospecting-assistant`.
- Non-goals for this milestone: TikTok/Instagram/YouTube publishing, Google Calendar → content_slots backfill, web/mobile frontend, concurrency.

Validation: `python3 -m pytest` (37 passed). Manually confirmed `generate_calendar.py --dry-run` is unchanged, and ran `process_content.py` against a real ffmpeg-synthesized video through real ffprobe/ffmpeg/faster-whisper — classification correctly failed cleanly (`CLASSIFICATION_FAILED`, file routed to `content/failed/`) with no `ANTHROPIC_API_KEY` configured in this environment. Live Claude classification against the real API was not exercised here.
