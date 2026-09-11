# Content Calendar — Current State

## Purpose

`PROJECT_STATE.md` is the compact current-state memory layer for this tool. It answers "what is true now" so agents do not need to load the full `CHANGELOG.md` by default.

This file is a derived working summary. Exact implementation authority remains: source code, active config, and tests first; then this file; then README and `docs/decisions/`; then targeted changelog history.

## Current Architecture

Two cooperating CLIs share one SQLite database (`data/content.db`, gitignored):

- `generate_calendar.py` — generates a month of posts from the fixed weekly schedule, pushes them to Google Calendar, and persists one `content_slots` row per event.
- `process_content.py` — discovers `.mov`/`.mp4` files in `content/incoming/`, inspects/transcribes/classifies each, and assigns confident classifications to the earliest matching open `content_slots` row.

Google Calendar remains the human-facing source of truth for the schedule; `content_slots` is an internal mirror `process_content.py` queries and claims. See `docs/decisions/0001-video-ingestion-pipeline.md` for the full rationale.

## Directory Ownership

- `generate_calendar.py`, `config.py`, `prompts.py` — existing schedule generation and Google Calendar push (pillar allocation, weekly mapping, fifth-Sunday rebalancing, prompt rotation). Unchanged except for the new `content_slots` persistence hook.
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
- `tests/test_slot_matcher.py` — earliest slot, multiple slots, occupied/past slots skipped, no slot available, two videos get different slots, double-assignment rejected.
- `tests/test_classification.py` — confidence gating, unknown-pillar rejection, null pillar, malformed-response rejection. No network call.
- `tests/test_content_store.py` — `content_slots`/`videos` idempotency primitives.
- `tests/test_generate_calendar.py` — locks existing `build_schedule` behavior (fifth-Sunday rule, determinism) and the new `content_slots` idempotency hook.
- `tests/test_process_content_integration.py` — full pipeline against a real ffmpeg-synthesized video with a fake `Transcriber`/`ContentClassifier` (no Whisper model download, no Claude call): high/low confidence, no-slot-available, dry-run caching, and rerun idempotency. Skipped automatically if ffmpeg/ffprobe are not on `PATH`.

Live Claude classification against the real API has not been exercised in this environment (no `ANTHROPIC_API_KEY` configured here) — verified instead via the unit-level validation contract plus a manual run that confirmed the real ffprobe/ffmpeg/faster-whisper path and a clean `CLASSIFICATION_FAILED` when the key is absent.

## Non-Goals (this milestone)

TikTok publishing, Instagram/YouTube publishing, a web/mobile frontend, multi-user auth, Google Calendar → `content_slots` backfill/import, video editing/clipping/thumbnails, concurrency/worker queues.
