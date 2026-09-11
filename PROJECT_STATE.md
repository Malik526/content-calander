# Content Calendar — Current State

## Purpose

`PROJECT_STATE.md` is the compact current-state memory layer for this tool. It answers "what is true now" so agents do not need to load the full `CHANGELOG.md` by default.

This file is a derived working summary. Exact implementation authority remains: source code, active config, and tests first; then this file; then README and `docs/decisions/`; then targeted changelog history.

## Current Architecture

Two cooperating CLIs share one SQLite database (`data/content.db`, gitignored):

- `generate_calendar.py` — generates a month of posts from a configurable posting cadence and weighted pillar allocation (`scheduling.py`), pushes them to Google Calendar, and persists one `content_slots` row per event.
- `process_content.py` — discovers `.mov`/`.mp4` files in `content/incoming/`, inspects/transcribes/classifies each, and assigns confident classifications to the earliest matching open `content_slots` row.

Google Calendar remains the human-facing source of truth for the schedule; `content_slots` is an internal mirror `process_content.py` queries and claims. See `docs/decisions/0001-video-ingestion-pipeline.md` and `docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md` for the full rationale.

## Directory Ownership

- `generate_calendar.py`, `config.py`, `prompts.py` — schedule generation and Google Calendar push. `config.py` defines cadence (`POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`) and per-pillar `weight`; `generate_calendar.build_schedule()` composes `scheduling.py` to turn that into dated, pillar-assigned posts, then attaches a rotated prompt from `prompts.py` if `PROMPT_GENERATION_ENABLED`.
- `scheduling.py` — pure date/allocation math, no I/O: `generate_posting_dates` (WHEN), `allocate_pillars` (largest-remainder counts) + `distribute_pillars` (smooth weighted round-robin ordering) (WHAT), `validate_schedule_config`. There is no more fixed weekday→pillar table or fifth-Sunday special case — see ADR-0002.
- `process_content.py` — thin orchestrator only: discover → inspect → transcribe → classify → confidence gate → slot match → persist → report. No vendor- or format-specific logic lives here.
- `media.py` — ffprobe inspection, sha256 content hashing, TikTok-compatibility check (informational only — no publishing in this milestone), mono WAV audio extraction for transcription. Never transcodes video.
- `transcription.py` — `Transcriber` interface; `FasterWhisperTranscriber` (local, CTranslate2-backed) is the only implementation.
- `classification.py` — `ContentClassifier` interface; `ClaudeClassifier` is the only implementation, forced to structured tool-use output, restricted to `config.CONTENT_TYPES` keys plus `null`.
- `slot_matcher.py` — one deterministic function: earliest `OPEN` `content_slots` row for a pillar, scheduled after now. No AI involvement.
- `content_store.py` — SQLite schema and all reads/writes for `videos` and `content_slots`, including the atomic slot-claim transaction.
- `content/incoming/`, `content/processed/`, `content/failed/` — file lifecycle for `process_content.py` (gitignored contents; directories tracked via `.gitkeep`).
- `data/` — SQLite database (gitignored).
- `tests/` — see Testing below.
- `docs/decisions/` — ADRs for this tool.

## Main Execution Paths

- `python3 generate_calendar.py --month 06 --year 2026`
- `python3 generate_calendar.py --month 06 --year 2026 --dry-run` (no Google Calendar push, no `content_slots` write)
- `python3 process_content.py`
- `python3 process_content.py --dry-run` (transcribes/classifies and caches results, never claims a slot or moves a file)
- `python3 process_content.py --verbose` (prints per-video media/transcript/classification detail)

## Scheduling Strategy

- **Cadence/days/time:** `config.POSTS_PER_WEEK` (1-7), `config.POSTING_DAYS` (`"auto"`, evenly-spaced by `scheduling.auto_posting_weekdays`, or an explicit weekday list overriding auto), `config.POSTING_TIME` (`"HH:MM"`). A month's slot count is derived from actual calendar dates for the chosen weekdays, never `posts_per_week * 4`.
- **Pillar allocation:** any number of `CONTENT_TYPES` entries, each with a `"weight"` (must sum to 1.0). `scheduling.allocate_pillars` uses the largest-remainder method for exact integer counts; ties break by config (dict) order. `scheduling.distribute_pillars` then orders those counts across the month so pillars interleave instead of clustering.
- **Prompts are optional:** attached only after date+pillar are decided, only if `config.PROMPT_GENERATION_ENABLED`, rotating per pillar through `PROMPTS[pillar_key]` (same sequential/wrap rule as before). `content_slots.prompt` is nullable and never participates in routing.
- **`content_slots` uniqueness is `scheduled_at` alone** (not `(scheduled_at, pillar_key)`) — one posting datetime is one slot regardless of which pillar a (possibly later-changed) strategy assigns it. A database created under the old two-column constraint is migrated automatically the first time it's opened (`content_store._migrate_content_slots_unique_constraint`).
- **Regenerating a month after changing the strategy is additive, not reconciling:** existing slots are never overwritten (`insert_slot_if_missing` skips any `scheduled_at` that already exists); only newly-covered timestamps get new slots. Mixing two strategies within one already-generated month is a known limitation, not a bug — see ADR-0002.

## Pipeline Behavior

- **Ordering:** videos are processed oldest-file-first by filesystem mtime, tie-broken by filename.
- **Identity/idempotency:** videos are keyed by sha256 content hash. A video already `ASSIGNED` or `FAILED` is skipped on a re-run and reported as such; a video interrupted mid-pipeline resumes from its last completed stage (inspected fields, transcript, and classification are all cached in `videos`).
- **Confidence gate:** `config.AUTO_ASSIGN_THRESHOLD` (default `0.80`, env `CONTENT_CALENDAR_AUTO_ASSIGN_THRESHOLD`). Below threshold, or a `null` classification, → `NEEDS_REVIEW`, never auto-assigned.
- **No slot available:** not a failure. Status stays `CLASSIFIED` with `assigned_slot_id = NULL`; the next run retries slot matching automatically once more slots exist.
- **Failure taxonomy** (`videos.failure_reason`): `NO_AUDIO_STREAM`, `UNSUPPORTED_CODEC`, `CORRUPT_MEDIA`, `TRANSCRIPTION_FAILED`, `CLASSIFICATION_FAILED`. A failed video moves to `content/failed/`; an assigned one moves to `content/processed/`; anything still `NEEDS_REVIEW` or waiting for a slot stays in `content/incoming/`.
- **Backfill:** out of scope. Only `content_slots` rows written by `generate_calendar.py` after this feature shipped are eligible for routing — months generated before this landed must be regenerated.

## External Services / Credentials

- Google Calendar via the shared service account (`~/growth_agency/credentials/service-account.json`) — unchanged.
- Anthropic/Claude for classification — `ANTHROPIC_API_KEY` in `.env` (same pattern as `internal-tools/content-analytics`). Model: `config.CLAUDE_MODEL` (default `claude-sonnet-5`).
- faster-whisper runs fully locally; no network call, no credential. First use downloads the model weights (`config.WHISPER_MODEL_SIZE`, default `base`) from Hugging Face.
- ffmpeg/ffprobe must be on `PATH` (system install, not a pip package). `process_content.py` checks this before touching any video and exits with install instructions if missing.

## Testing

`python3 -m pytest` (config: `pytest.ini`, root `conftest.py` puts the tool directory on `sys.path`).

- `tests/test_media.py` — ffprobe mocked; MP4+H.264, MOV+H.264, MOV+HEVC, no-audio, corrupt, unsupported-codec, TikTok-compatibility, hashing.
- `tests/test_scheduling.py` — posting-date generation (leap/non-leap Feb, 30/31-day months, explicit/auto days, 1-7 posts/week, posting time, ascending/in-month), auto-weekday distribution, pillar allocation (largest remainder, ties, 1..N pillars, invalid weights), pillar sequencing (exact counts, determinism, no clustering), and all section-17 validation cases.
- `tests/test_slot_matcher.py` — earliest slot, multiple slots, occupied/past slots skipped, no slot available, two videos get different slots, double-assignment rejected.
- `tests/test_classification.py` — confidence gating, unknown-pillar rejection, null pillar, malformed-response rejection. No network call.
- `tests/test_content_store.py` — `content_slots`/`videos` idempotency primitives, plus the old→new unique-constraint migration.
- `tests/test_generate_calendar.py` — `build_schedule` composition of `scheduling.py`, determinism, prompt attachment, event-body construction, `content_slots` idempotency, and the new scheduled_at-only uniqueness invariant.
- `tests/test_process_content_integration.py` — full pipeline against a real ffmpeg-synthesized video with a fake `Transcriber`/`ContentClassifier` (no Whisper model download, no Claude call): high/low confidence, no-slot-available, dry-run caching, and rerun idempotency. Skipped automatically if ffmpeg/ffprobe are not on `PATH`.

Live Claude classification against the real API has not been exercised in this environment (no `ANTHROPIC_API_KEY` configured here) — verified instead via the unit-level validation contract plus a manual run that confirmed the real ffprobe/ffmpeg/faster-whisper path and a clean `CLASSIFICATION_FAILED` when the key is absent.

## Non-Goals (this milestone)

TikTok publishing, Instagram/YouTube publishing, a web/mobile frontend, multi-user auth, Google Calendar → `content_slots` backfill/import, video editing/clipping/thumbnails, concurrency/worker queues.
