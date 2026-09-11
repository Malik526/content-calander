# ADR-0001: Video Ingestion Pipeline — Persistent Slots, Local Transcription, Claude Classification

## Status

Accepted

## Context

`content-calendar` generated a monthly posting schedule and pushed it to Google Calendar, but had no way to route an actual recorded video to one of those slots — a human had to look at the calendar and decide by hand.

We needed a first automation milestone: drop `.mov`/`.mp4` files into an incoming directory, have the system transcribe and classify them against the existing content pillars, and assign each to the earliest matching future slot — without inventing a bigger platform than this one-user tool needs.

## Decision

- **Persistent slots.** `generate_calendar.py` now writes one `content_slots` row per event it pushes to Google Calendar (`content_store.py`), in addition to its existing behavior. Google Calendar stays the human-facing calendar; `content_slots` is the internal state `process_content.py` queries and claims. Insertion is idempotent on `(scheduled_at, pillar_key)`, so re-running generation for a month that already has persisted slots does not duplicate them (Google Calendar events themselves can still duplicate on rerun — that is pre-existing behavior, unchanged here).
- **AI decides the pillar; code decides the date.** Classification (`classification.py`) is the only place an LLM makes a decision. Slot selection (`slot_matcher.py`) is a plain deterministic query: earliest `OPEN` slot for the classified pillar, scheduled after now. This keeps the assignment auditable and reproducible.
- **Confidence gate.** A video is only auto-assigned when the classifier returns a configured pillar key with confidence >= `config.AUTO_ASSIGN_THRESHOLD` (default 0.80). Below that, or on a `null` pillar, the video is marked `NEEDS_REVIEW` and never silently forced into a pillar.
- **Local transcription (faster-whisper).** This is a batch desktop tool, not a live UI — there is no latency pressure. Local transcription avoids a per-minute API bill and an extra vendor credential, and keeps video off the network. `faster-whisper` (CTranslate2-backed) was chosen over the reference Whisper implementation for lower memory/CPU cost. It sits behind a `Transcriber` interface so a hosted alternative can be swapped in later without touching the orchestrator.
- **Claude for classification.** The repository family already has an Anthropic integration pattern and `.env` convention (`internal-tools/content-analytics/analysis/claude_analysis.py`); introducing a second LLM vendor for this one task would add a credential and a code path for no real benefit. Classification is forced to structured output via Claude tool-use (`classification.ClaudeClassifier`), never parsed prose, and sits behind a `ContentClassifier` interface.
- **Pass-through media, not blanket conversion.** `media.py` inspects with `ffprobe` and extracts only a small mono 16kHz WAV for transcription; the original video is never re-encoded. TikTok's Content Posting API already accepts MOV/HEVC directly, so there is no publishing-driven reason to transcode iPhone footage in this milestone (publishing itself is out of scope — see Non-Goals).
- **ffmpeg/ffprobe are a system prerequisite, not a Python dependency.** `media.check_ffmpeg_available()` checks `PATH` before any video is touched and fails with install instructions rather than silently degrading.
- **Small module surface for this milestone.** No `media/`, `transcription/`, `classification/`, `scheduling/`, `persistence/` package tree, and no `publishing/tiktok.py` stub yet — one file per concern (`media.py`, `transcription.py`, `classification.py`, `content_store.py`, `slot_matcher.py`) per `~/.agents/CODING.md`'s modularity rule (abstraction only when it reduces real duplication). Split a module further only when it actually grows large enough to need it. TikTok publishing is a separate future milestone; nothing here should assume its shape.

## Rationale

Reusing the existing calendar allocation logic (weekly schedule, fifth-Sunday rebalancing, prompt rotation) instead of duplicating it means the two systems can never drift on "what pillar belongs on what date" — `generate_calendar.py` remains the single source for that decision; `process_content.py` only ever asks "what's the next open slot for this pillar."

Separating classification (AI) from slot matching (deterministic) means a routing mistake is always traceable to one of two causes: a bad classification (visible in `classification_reason`) or a scheduling bug (visible in a `content_slots` query) — never an entangled black box.

## Consequences

- `videos` and `content_slots` (SQLite, `content_store.py`) are new persistent state for this tool; back them up like any other tool database if the pipeline is depended on.
- A video with no matching open slot is *not* a failure (`status=CLASSIFIED`, `assigned_slot_id=NULL`); it is picked up automatically the next time `process_content.py` runs after more slots exist.
- Backfill is explicitly out of scope: only internally persisted `content_slots` are eligible for automatic routing. If future months were generated with Google Calendar before this migration landed, they must be regenerated (or a one-time importer must be built later) before `process_content.py` has anything to assign into. A Google Calendar → DB importer is only worth building if this tool needs to onboard a user with an already-established external schedule.
- `AUTO_ASSIGN_THRESHOLD`, `ANTHROPIC_API_KEY`, and the `faster-whisper` model settings are new required/optional configuration (see `.env.example`).

## Guardrails

- Do not let TikTok-specific rules leak into `classification.py` or `slot_matcher.py`. `media.is_tiktok_compatible()` is informational only in this milestone.
- Do not have the classifier (or any LLM call) choose a `content_slots` row directly — pillar selection and date selection stay in separate functions.
- Do not add `publishing/tiktok.py` or any TikTok API calls until the next milestone explicitly scopes them.
- Keep `Transcriber` and `ContentClassifier` as the only integration points for their respective vendors; do not call `faster_whisper` or `anthropic` directly from `process_content.py`.

## Current Implementation

- `content_store.py` — `videos` / `content_slots` schema and queries.
- `media.py` — ffprobe inspection, TikTok-compatibility check, audio extraction.
- `transcription.py` — `Transcriber` interface, `FasterWhisperTranscriber`.
- `classification.py` — `ContentClassifier` interface, `ClaudeClassifier`.
- `slot_matcher.py` — deterministic earliest-open-slot selection.
- `process_content.py` — thin orchestrator, CLI, restart-safe per-video pipeline.
- `generate_calendar.py` — extended to persist `content_slots` alongside existing Google Calendar push.
- `tests/` — media inspection, scheduling, classification validation, content_store idempotency, and a full-pipeline integration suite (fake transcriber/classifier, real ffmpeg-synthesized video).
