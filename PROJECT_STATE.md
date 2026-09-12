# Content Calendar — Current State

## Purpose

`PROJECT_STATE.md` is the compact current-state memory layer for this tool. It answers "what is true now" so agents do not need to load the full `CHANGELOG.md` by default.

This file is a derived working summary. Exact implementation authority remains: source code, active config, and tests first; then this file; then README and `docs/decisions/`; then targeted changelog history.

## Current Architecture

Two cooperating CLIs share one SQLite database (`data/content.db`, gitignored):

- `generate_calendar.py` — generates a month of posts from a configurable posting cadence and weighted pillar allocation (`scheduling.py`), pushes them to Google Calendar, and persists one `content_slots` row per event.
- `process_content.py` — discovers `.mov`/`.mp4` files in `content/incoming/`, inspects/transcribes/classifies each, and assigns confident classifications to the earliest matching open `content_slots` row.

Google Calendar remains the human-facing source of truth for the schedule; `content_slots` is an internal mirror `process_content.py` queries and claims. See `docs/decisions/0001-video-ingestion-pipeline.md`, `docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md`, `docs/decisions/0003-local-embedding-classification.md`, and `docs/decisions/0004-dedicated-google-calendar-ownership.md` for the full rationale.

The default `process_content.py` pipeline is fully local and requires no API key: ffmpeg + faster-whisper + local embedding classification (`fastembed` + `BAAI/bge-small-en-v1.5`) + SQLite. Claude remains available as an optional classifier (`config.CLASSIFIER=claude`) for comparison/benchmarking via `evaluate_classifier.py`.

## Active Pillar Strategy (Provisional)

As of 2026-09-12, `config.CONTENT_TYPES` is a **provisional** engineering-focused pillar set, replacing the earlier agency-oriented one, to test classification/routing against the content actually being produced now:

- `engineering` — **Software Engineering & Building** — 40%
- `career` — **Early-Career Software Engineering** — 30%
- `building_in_public` — **Building in Public** — 20%
- `mindset` — **Mindset & Discipline** — 10% (key unchanged from the prior strategy; label/description/weight updated)

This is a content-only change — no classifier, scheduling, persistence, or transcription architecture changed. The old pillar keys (`acquisition`, `building`, `execution`) no longer exist anywhere in `config.py`; `prompts.py` retired their prompt lists.

`prompts.py`'s `engineering`/`career`/`building_in_public` entries are **minimal placeholders** (3 short prompts each) added only to keep `PROMPT_GENERATION_ENABLED=True` functional — not a designed content plan. `mindset`'s prompt list is unchanged from the old agency-era strategy (same key, stale content) and does not yet reflect the new framing; designing real prompts for all four pillars is separate follow-up work. Prompts remain optional and never affect routing either way.

`EMBEDDING_MIN_SIMILARITY`/`EMBEDDING_MIN_MARGIN` were calibrated (loosely) against the old pillar set's semantics; they have **not** been re-validated against the new pillars with real transcripts. Treat them as still-uncalibrated placeholders until `evaluate_classifier.py --sweep` is run against a real labeled dataset built from the new content direction.

## Calendar Ownership

As of 2026-09-12, normal operation (`generate_calendar.py`/`clear_calendar.py` with no `--calendar` flag) targets exactly one dedicated, app-owned Google Calendar (`config.APP_CALENDAR_SUMMARY`, default `"Content Automation"`) — never `"primary"`, never an arbitrary calendar. Created/owned via OAuth (the human account), not the shared service account — see `docs/decisions/0004-dedicated-google-calendar-ownership.md`.

- `calendar_manager.resolve_app_calendar()` reuses the calendar ID persisted in `data/calendar_state.json` (gitignored); recovers by owner-only name search if that state is lost/inaccessible; creates a new calendar only if neither works. A calendar is created at most once per account.
- `clear_calendar.py`'s default path deletes only `content_slots` rows in `OPEN` status and their exact tracked calendar event — not a calendar-wide date-range wipe. `--all` extends this to `ASSIGNED` slots too, resetting the referencing video to `status=CLASSIFIED`/`assigned_slot_id=NULL` first (required by the FK on `videos.assigned_slot_id`) without touching its transcript/classification. The calendar itself is never deleted by either mode.
- `--calendar <id>` remains an explicit, opt-in override on both scripts, using the shared service account exactly as before this milestone (unchanged code) — never the default.
- **Not verified live in this environment**: creating this ADR's implementation required no Google Cloud OAuth Client ID to exist yet, and completing the interactive OAuth consent flow requires a real browser and the user's own Google Cloud Console access — neither available here. All calendar-ownership logic is verified via mocked-API tests (`tests/test_calendar_manager.py`, `tests/test_calendar_target.py`, `tests/test_clear_calendar.py`) plus real (unmocked) runs confirming dry-run touches nothing and the missing-OAuth-setup error is clear and fails closed. The one-time OAuth setup (README.md "Calendar Ownership" step) and the full live walkthrough (a real calendar actually appearing under the account, primary staying untouched) remain the user's manual step.

## Directory Ownership

- `generate_calendar.py`, `config.py`, `prompts.py` — schedule generation and Google Calendar push. `config.py` defines cadence (`POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`) and per-pillar `weight`; `generate_calendar.build_schedule()` composes `scheduling.py` to turn that into dated, pillar-assigned posts, then attaches a rotated prompt from `prompts.py` if `PROMPT_GENERATION_ENABLED`. `--calendar` omitted (normal case) resolves the dedicated app calendar via `calendar_manager.py`; `--calendar <id>` is an explicit override via the service account.
- `calendar_manager.py` — OAuth auth (`build_oauth_calendar_service`) and dedicated-calendar resolution/creation/persistence (`resolve_app_calendar`, `load_calendar_state`/`save_calendar_state`). No default/implicit path ever returns `"primary"`.
- `clear_calendar.py` — clears the dedicated calendar's `content_slots`-tracked events (`OPEN`, or `OPEN`+`ASSIGNED` with `--all`); `--calendar <id>` preserves the original service-account/date-range clear as an explicit override.
- `scheduling.py` — pure date/allocation math, no I/O: `generate_posting_dates` (WHEN), `filter_future_dates` (discard past candidates before allocation — see Scheduling Strategy below), `allocate_pillars` (largest-remainder counts) + `distribute_pillars` (smooth weighted round-robin ordering) (WHAT), `validate_schedule_config`. There is no more fixed weekday→pillar table or fifth-Sunday special case — see ADR-0002.
- `process_content.py` — thin orchestrator only: discover → inspect → transcribe → classify → confidence gate → slot match → persist → report. No vendor- or format-specific logic lives here.
- `media.py` — ffprobe inspection, sha256 content hashing, TikTok-compatibility check (informational only — no publishing in this milestone), mono WAV audio extraction for transcription. Never transcodes video.
- `transcription.py` — `Transcriber` interface; `FasterWhisperTranscriber` (local, CTranslate2-backed) is the only implementation.
- `classification.py` — `ContentClassifier` interface, `ClassificationResult`, `build_classifier()` (reads `config.CLASSIFIER`). `EmbeddingClassifier` (default, fully local via `fastembed`) and `ClaudeClassifier` (optional, structured tool-use output) both restrict `pillar` to `config.CONTENT_TYPES` keys plus `None`, and each applies its own auto-assign policy before returning — see ADR-0003.
- `slot_matcher.py` — one deterministic function: earliest `OPEN` `content_slots` row for a pillar, scheduled after now. No AI involvement.
- `content_store.py` — SQLite schema and all reads/writes for `videos` and `content_slots`, including the atomic slot-claim transaction.
- `content/incoming/`, `content/processed/`, `content/failed/` — file lifecycle for `process_content.py` (gitignored contents; directories tracked via `.gitkeep`).
- `data/` — SQLite database (gitignored).
- `evaluate_classifier.py` — offline benchmark harness for `ContentClassifier` implementations against a local labeled dataset; never imports `content_store`.
- `evaluation/` — private golden-evaluation corpus (`labels.csv`, `transcripts/*.txt`), gitignored except `evaluation/README.md`.
- `tests/` — see Testing below. `tests/fixtures/eval_sample/` is a small committed synthetic dataset for testing `evaluate_classifier.py` itself.
- `docs/decisions/` — ADRs for this tool.

## Main Execution Paths

- `python3 generate_calendar.py --month 06 --year 2026` (targets the dedicated app calendar via OAuth)
- `python3 generate_calendar.py --month 06 --year 2026 --dry-run` (no Google Calendar push, no `content_slots` write, no calendar resolved/created)
- `python3 generate_calendar.py --month 06 --year 2026 --calendar <id>` (advanced/debug override, service account)
- `python3 clear_calendar.py [--dry-run] [--all]` (clears the dedicated calendar's tracked schedule)
- `python3 clear_calendar.py --calendar <id> [--start ...] [--end ...]` (advanced/debug override, service account)
- `python3 process_content.py`
- `python3 process_content.py --dry-run` (transcribes/classifies and caches results, never claims a slot or moves a file)
- `python3 process_content.py --verbose` (prints per-video media/transcript/classification detail)
- `python3 evaluate_classifier.py --classifier embeddings` / `--classifier claude` (benchmark against `evaluation/`)
- `python3 evaluate_classifier.py --classifier embeddings --sweep` (threshold sweep; see Classification below)

## Scheduling Strategy

- **Cadence/days/time:** `config.POSTS_PER_WEEK` (1-7), `config.POSTING_DAYS` (`"auto"`, evenly-spaced by `scheduling.auto_posting_weekdays`, or an explicit weekday list overriding auto), `config.POSTING_TIME` (`"HH:MM"`). A month's slot count is derived from actual calendar dates for the chosen weekdays, never `posts_per_week * 4`.
- **Future-only (as of 2026-09-12):** `scheduling.filter_future_dates` discards any candidate posting datetime before `start_at` **before** pillar allocation runs — `generate_calendar.build_schedule(year, month, start_at=None)` resolves `start_at` via `slot_matcher.now_in_config_timezone()` when omitted (the same "what does now mean" convention slot matching already uses; `build_schedule` never calls `datetime.now()` itself). The comparison is inclusive (`scheduled_at >= start_at`), so a same-day slot is kept if its `POSTING_TIME` hasn't passed yet. A month already partly elapsed is allocated against only its remaining count, not the full month; an entirely past month returns `[]` and `generate_calendar.py` prints `No future posting slots remain for <Month> <Year>.` and returns before touching Google Calendar or `ContentStore` at all. A future month is unaffected (every candidate clears the boundary).
- **Pillar allocation:** any number of `CONTENT_TYPES` entries, each with a `"weight"` (must sum to 1.0). `scheduling.allocate_pillars` uses the largest-remainder method for exact integer counts (computed against the future-filtered date count, not the full month); ties break by config (dict) order. `scheduling.distribute_pillars` then orders those counts across the month so pillars interleave instead of clustering.
- **Prompts are optional:** attached only after date+pillar are decided, only if `config.PROMPT_GENERATION_ENABLED`, rotating per pillar through `PROMPTS[pillar_key]` (same sequential/wrap rule as before). `content_slots.prompt` is nullable and never participates in routing.
- **`content_slots` uniqueness is `scheduled_at` alone** (not `(scheduled_at, pillar_key)`) — one posting datetime is one slot regardless of which pillar a (possibly later-changed) strategy assigns it. A database created under the old two-column constraint is migrated automatically the first time it's opened (`content_store._migrate_content_slots_unique_constraint`).
- **Regenerating a month after changing the strategy is additive, not reconciling:** existing slots are never overwritten (`insert_slot_if_missing` skips any `scheduled_at` that already exists); only newly-covered timestamps get new slots. Mixing two strategies within one already-generated month is a known limitation, not a bug — see ADR-0002.

## Pipeline Behavior

- **Ordering:** videos are processed oldest-file-first by filesystem mtime, tie-broken by filename.
- **Identity/idempotency:** videos are keyed by sha256 content hash. A video already `ASSIGNED` or `FAILED` is skipped on a re-run and reported as such; a video interrupted mid-pipeline resumes from its last completed stage (inspected fields, transcript, and classification are all cached in `videos`).
- **Confidence gate:** each classifier applies its own policy and returns `pillar=None` to abstain — `process_content.py`'s gate is simply `eligible = result.pillar is not None`. See Classification below for what each classifier's gate actually checks.
- **No slot available:** not a failure. Status stays `CLASSIFIED` with `assigned_slot_id = NULL`; the next run retries slot matching automatically once more slots exist.
- **Failure taxonomy** (`videos.failure_reason`): `NO_AUDIO_STREAM`, `UNSUPPORTED_CODEC`, `CORRUPT_MEDIA`, `TRANSCRIPTION_FAILED`, `CLASSIFICATION_FAILED`, `MODEL_LOAD_FAILED`, `EMBEDDING_FAILED`, `INVALID_PILLAR_CONFIGURATION`. A failed video moves to `content/failed/`; an assigned one moves to `content/processed/`; anything still `NEEDS_REVIEW` or waiting for a slot stays in `content/incoming/`.
- **Backfill:** out of scope. Only `content_slots` rows written by `generate_calendar.py` after this feature shipped are eligible for routing — months generated before this landed must be regenerated.

## Classification

- **Default: `EmbeddingClassifier`** (`config.CLASSIFIER=embeddings`). Fully local via `fastembed` + `BAAI/bge-small-en-v1.5` (384-dim, ~65MB, cached at `config.EMBEDDING_CACHE_DIR`). Each pillar's `label + description + classification_examples` is combined into one profile text and embedded once per run; a transcript is embedded and compared to every pillar by cosine similarity. Auto-assigns only if the top pillar's similarity clears `config.EMBEDDING_MIN_SIMILARITY` **and** its margin over the second-best pillar clears `config.EMBEDDING_MIN_MARGIN` — otherwise `NEEDS_REVIEW`. **Both thresholds are explicitly uncalibrated placeholders** (`0.50`/`0.03`); use `evaluate_classifier.py --sweep` against real labeled transcripts to pick real values. `ClassificationResult.confidence` for this classifier is a raw cosine similarity, not a calibrated probability — never label it "% confidence" in output.
- **Optional: `ClaudeClassifier`** (`config.CLASSIFIER=claude`). Unchanged behavior from Milestone 1 except that it now applies `config.AUTO_ASSIGN_THRESHOLD` internally (previously this lived in `process_content.py`) and returns `pillar=None` below it. Requires `ANTHROPIC_API_KEY`; its absence still fails cleanly per-video (`CLASSIFICATION_FAILED`), not at startup.
- **Selection:** `classification.build_classifier()`, reading `config.CLASSIFIER`. An unknown value fails immediately: `Unsupported CLASSIFIER='foo'. Valid values: embeddings, claude`.
- **Evaluation:** `evaluate_classifier.py --classifier embeddings|claude [--sweep]` against `evaluation/` (private, real transcripts) or any `--dataset` path (e.g. the committed `tests/fixtures/eval_sample/`). Reports total/auto-assigned/review, auto-assigned accuracy, **wrong auto-assignments** (the metric to minimize), per-pillar accuracy, confusion pairs, and latency. Never touches `data/content.db`.

## External Services / Credentials

- Google Calendar, normal path: OAuth as the human account (`config.CALENDAR_OAUTH_CLIENT_SECRETS_PATH`/`CALENDAR_OAUTH_TOKEN_PATH`, both under `~/.config/content-calendar/`) — see Calendar Ownership above.
- Google Calendar, `--calendar` override only: the shared service account (`~/growth_agency/credentials/service-account.json`) — unchanged from before this milestone.
- Anthropic/Claude — optional, only needed for `config.CLASSIFIER=claude` or `evaluate_classifier.py --classifier claude`. `ANTHROPIC_API_KEY` in `.env` (same pattern as `internal-tools/content-analytics`). Model: `config.CLAUDE_MODEL` (default `claude-sonnet-5`).
- faster-whisper and the embedding model both run fully locally; no network call, no credential, after their one-time model downloads (`config.WHISPER_MODEL_SIZE`/`config.EMBEDDING_MODEL`, both from Hugging Face).
- ffmpeg/ffprobe must be on `PATH` (system install, not a pip package). `process_content.py` checks this before touching any video and exits with install instructions if missing.

## Testing

`python3 -m pytest` (config: `pytest.ini`, root `conftest.py` puts the tool directory on `sys.path`).

- `tests/test_media.py` — ffprobe mocked; MP4+H.264, MOV+H.264, MOV+HEVC, no-audio, corrupt, unsupported-codec, TikTok-compatibility, hashing.
- `tests/test_calendar_manager.py` — dedicated-calendar resolution/creation/recovery/persistence and OAuth credential handling, all Google API calls mocked: reuse, create-if-missing, recovery via owner-only name search, fail-closed when `create_if_missing=False`, never adopts a merely-shared calendar, never returns `"primary"`.
- `tests/test_calendar_target.py` — `generate_calendar.py`'s `--calendar` default is `None`; dry-run touches neither OAuth nor the service account; the default path uses OAuth + the dedicated calendar id and never the service account; the explicit override uses the service account and never OAuth.
- `tests/test_clear_calendar.py` — dry-run performs no deletion; default clear removes only `OPEN` slots + their events and leaves `ASSIGNED` untouched; `--all` also clears `ASSIGNED` slots and resets the video; the calendar row itself is never deleted; no dedicated calendar configured fails closed (never falls back to `"primary"`); the explicit `--calendar` override still works via the service account.
- `tests/test_scheduling.py` — posting-date generation (leap/non-leap Feb, 30/31-day months, explicit/auto days, 1-7 posts/week, posting time, ascending/in-month), auto-weekday distribution, pillar allocation (largest remainder, ties, 1..N pillars, invalid weights), pillar sequencing (exact counts, determinism, no clustering), all section-17 validation cases, and `filter_future_dates` (past-date removal, same-day boundary inclusive, future/entirely-past months, ascending order preserved).
- `tests/test_future_only_schedule.py` — `build_schedule`'s future-only integration: default `start_at` resolves via `slot_matcher.now_in_config_timezone`, pillar reallocation against the filtered (not full-month) count, future months unaffected, entirely-past month returns `[]`, only future rows get persisted, and `generate_calendar.main()` prints the clean "no future slots" message with zero Calendar/DB writes for a past month (both real and `--dry-run`).
- `tests/test_slot_matcher.py` — earliest slot, multiple slots, occupied/past slots skipped, no slot available, two videos get different slots, double-assignment rejected.
- `tests/test_config.py` — locks the active pillar strategy: exact key set (`engineering`/`career`/`building_in_public`/`mindset`), weights summing to 1.0, every pillar has a description and examples, default classifier is `embeddings`, `build_classifier()` initializes with no `ANTHROPIC_API_KEY`, and `EmbeddingClassifier` builds a profile for all four pillars.
- `tests/test_classification.py` — Claude's `_validate_result` contract: confidence gating, unknown-pillar rejection, null pillar, malformed-response rejection. Uses arbitrary local pillar keys (not `config.CONTENT_TYPES`) so it stays valid regardless of the active pillar strategy. No network call.
- `tests/test_embedding_classifier.py` — `EmbeddingClassifier` with a mocked embedding model: single/multi-pillar classification, score ordering, low-similarity and small-margin review, high-score assignment, empty transcript, 1..N pillars, pillar-vector caching, invalid pillar config, invalid thresholds, and classifier selection (`build_classifier`, unknown-value rejection).
- `tests/test_evaluate_classifier.py` — dataset loading (incl. the committed `tests/fixtures/eval_sample/`), metrics from known predictions, confusion-pair counting, deterministic threshold-sweep math over pre-computed scores, and a static check that the harness never imports `content_store`.
- `tests/test_content_store.py` — `content_slots`/`videos` idempotency primitives, the old→new unique-constraint migration, the videos-table column migration, and `list_slots_by_status`/`delete_slot`/`unassign_video_for_slot` (including the FK-enforced rule that an `ASSIGNED` slot can't be deleted until its video is unassigned).
- `tests/test_generate_calendar.py` — `build_schedule` composition of `scheduling.py`, determinism, prompt attachment, event-body construction, `content_slots` idempotency, and the new scheduled_at-only uniqueness invariant.
- `tests/test_process_content_integration.py` — full pipeline against a real ffmpeg-synthesized video with a fake `Transcriber`/`ContentClassifier` (no Whisper model download, no Claude/embedding call): high/low confidence, no-slot-available, dry-run caching, and rerun idempotency. `FakeClassifier` applies its own threshold internally, mirroring the real self-gating classifier contract. Skipped automatically if ffmpeg/ffprobe are not on `PATH`.

Live Claude classification against the real API has not been exercised in this environment (no `ANTHROPIC_API_KEY` configured here) — verified instead via the unit-level validation contract plus manual runs confirming: the real ffprobe/ffmpeg/faster-whisper/embeddings path with no API key at all; `CLASSIFIER=foo` failing immediately; `CLASSIFIER=claude` with no key still failing cleanly per-video; and `evaluate_classifier.py` (both plain and `--sweep`) against the real embedding model and the committed synthetic fixture.

## Non-Goals (this milestone)

TikTok publishing, Instagram/YouTube publishing, a web/mobile frontend, multi-user auth, Google Calendar → `content_slots` backfill/import, video editing/clipping/thumbnails, concurrency/worker queues, Gemma classifier, vector database, cloud embedding API.
