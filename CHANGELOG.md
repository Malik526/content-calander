# Content Calendar — Changelog

## 2026-09-11

### Video Ingestion Pipeline — Milestone 1

Added the first automated content-processing workflow on top of the existing calendar generator: drop `.mov`/`.mp4` files into `content/incoming/`, run `python3 process_content.py`, and have the system inspect, transcribe, classify, and route each video to the earliest matching future posting slot without manual assignment.

- Added `content_store.py` (SQLite `videos` + `content_slots` tables), `media.py` (ffprobe inspection, TikTok-compatibility check, audio extraction), `transcription.py` (`Transcriber` interface, `FasterWhisperTranscriber`), `classification.py` (`ContentClassifier` interface, `ClaudeClassifier` via forced tool-use), `slot_matcher.py` (deterministic earliest-open-slot selection), and `process_content.py` (thin orchestrator + CLI).
- Extended `generate_calendar.py` to persist a `content_slots` row per generated event alongside the existing Google Calendar push, idempotently on `(scheduled_at, pillar_key)` so re-running a month does not duplicate slots. Existing CLI, allocation logic, weekly mapping, fifth-Sunday rebalancing, prompt rotation, and dry-run behavior are unchanged.
- Extended `config.py` with pillar descriptions (for the classifier), `AUTO_ASSIGN_THRESHOLD`, transcription/classification vendor settings, file lifecycle directories, and TikTok generic compatibility targets.
- Added `pytest` suite (`tests/`) covering media inspection, scheduling, classification-policy validation, content_store idempotency, existing calendar-generation behavior, and a full-pipeline integration test against a real ffmpeg-synthesized video.
- Added `docs/decisions/0001-video-ingestion-pipeline.md` (architecture decision) and `PROJECT_STATE.md` (current-state snapshot), following the pattern already established in `internal-tools/service-business-prospecting-assistant`.
- Non-goals for this milestone: TikTok/Instagram/YouTube publishing, Google Calendar → content_slots backfill, web/mobile frontend, concurrency.

Validation: `python3 -m pytest` (37 passed). Manually confirmed `generate_calendar.py --dry-run` is unchanged, and ran `process_content.py` against a real ffmpeg-synthesized video through real ffprobe/ffmpeg/faster-whisper — classification correctly failed cleanly (`CLASSIFICATION_FAILED`, file routed to `content/failed/`) with no `ANTHROPIC_API_KEY` configured in this environment. Live Claude classification against the real API was not exercised here.
