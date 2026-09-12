# ADR-0005: FIFO Baseline Routing, Optional Pillar Strategy, and First-Class Captions

## Status

Accepted

## Context

Through Milestone 1.2, scheduling a video required a confident pillar classification: `discover → inspect → transcribe → classify → confidence gate → slot match → persist`. Classifier abstention (low similarity/margin, empty transcript, out-of-pillar content) left a video in `NEEDS_REVIEW`, unscheduled, regardless of whether the video itself was perfectly postable.

The actual V1 product need is narrower and more valuable on its own: *"drop in finished videos, pick a cadence, they get scheduled first-in-first-out."* Classification into content pillars is a strategy layer a creator might opt into later, not a prerequisite for basic automation. This milestone makes that the default, while preserving the classify-then-match strategy as an explicit, fully-functional opt-in mode.

## Decision

### Routing modes

`config.ROUTING_MODE` (`CONTENT_CALENDAR_ROUTING_MODE`, default **`"fifo"`**) selects between:

- **`fifo`** — `generate_calendar.build_schedule()` produces untyped slots (`content_type=None`, no `allocate_pillars`/`distribute_pillars` call, no prompt attached) straight from the posting-cadence + future-only date set. `process_content.py` never constructs a `ContentClassifier` in this mode (`classification.build_classifier()` is simply not called) — an invalid or unset `CONTENT_CALENDAR_CLASSIFIER` cannot block FIFO scheduling, and no embedding-model load cost is paid for a stage FIFO doesn't need. Matching is `slot_matcher.select_slot_fifo` — earliest `OPEN` slot at or after now (inclusive), no pillar filter.
- **`pillar`** — unchanged Milestone-1/1.1/1.2 behavior: weighted allocation at generation time (`allocate_pillars` + `distribute_pillars`), classify → confidence gate → pillar-specific `slot_matcher.select_slot` at ingestion time.

`scheduling.validate_routing_mode()` fails immediately and clearly on an unrecognized value (mirrors `classification.build_classifier`'s `UnsupportedClassifierError` message style), called at first use in both `generate_calendar.main()` and `process_content.main()` — never inferred from whether `CONTENT_TYPES` happens to be populated, per explicit product direction.

### Schema

`content_slots.pillar_key` is now nullable (was `NOT NULL`). `UNIQUE(scheduled_at)` is preserved unchanged — one posting datetime is one slot regardless of routing mode. `content_store._content_slots_needs_migration` now detects *either* the old two-column unique constraint (Milestone 1.1) or a `NOT NULL` `pillar_key` (pre-Milestone-1.3) and runs one rebuild pass against the current schema either way; existing rows are untouched since they already carry non-null `pillar_key` values.

No new `videos.ingested_at`/`queue_position` column — `videos.created_at` (set once in `insert_video`, never updated) already is a durable first-seen timestamp, reused directly for FIFO ordering (see below).

### Transcription is decoupled from scheduling in FIFO mode — deliberately, not in pillar mode

This is the core architectural change beyond what the original milestone brief's own "smallest implementation" framing asked for, added after product review: **a valid, inspected video must be schedulable in FIFO mode even if transcription fails.** Media inspection (ffprobe validity, audio stream presence) stays a hard blocking gate in both modes — it's a precondition ("does a schedulable file exist"), not content intelligence. Transcription is different: it's enrichment, and FIFO's entire premise is that scheduling must not depend on content intelligence succeeding.

Mechanism: a transcription failure in FIFO mode sets `videos.status = "TRANSCRIPTION_FAILED"` (a new, FIFO-only status) and `transcription_status = "FAILED"` with `failure_reason` from the existing taxonomy — **without** moving the file to `content/failed/` or aborting `process_one()`. The video falls through to the caption stage (which records `caption_source = "none"`, since there's nothing to derive from) and then to `slot_matcher.select_slot_fifo` in the *same call*. A rerun does not re-attempt transcription (guarded by `status != "TRANSCRIPTION_FAILED"`) but does keep retrying slot matching until one is available, exactly like the existing `WAITING_FOR_SLOT` retry behavior. In pillar mode, transcription failure is unchanged: still a hard `FAILED`, moved to `content/failed/` — classification genuinely has nothing to work with otherwise.

**Deliberate deviation from a literal "assign slot, then transcribe" ordering.** An earlier draft of this decision considered reordering the pipeline so slot assignment happens *before* transcription in FIFO mode, to more visibly express "scheduling doesn't wait on transcription." That was rejected: `assign_slot()` also moves the file out of `content/incoming/` and sets a status (`ASSIGNED`) that the top-of-function guard treats as terminal/skip-on-rerun. If the process were interrupted between slot assignment and transcription under that ordering, the video would become permanently undiscoverable with transcription never attempted — silently losing transcript data forever, which this milestone explicitly must not do. Keeping `transcribe (non-blocking on failure) → caption → slot-match` achieves the identical product outcome (a video schedules in the same run regardless of transcription's success or failure) without that crash window.

### Captions are first-class video metadata

`videos.caption_text`/`caption_source` (both nullable `TEXT`, added via the existing simple `ALTER TABLE` migration pattern) exist independently of any publishing step. `config.CAPTION_MODE` (default `"transcript_auto"`) governs derivation, applied in **both** routing modes right after the transcribe stage:

- `transcript_auto` — `caption.build_caption_from_transcript()` normalizes whitespace only; **no truncation**. A caption should not conceptually be "a TikTok-sized caption" — this repository will eventually publish the same video to multiple platforms with different length limits, so platform-specific truncation is deferred to the eventual per-platform publisher boundary, not baked into the canonical stored value. Falls back to `caption_source = "none"` when the transcript is permanently unavailable (`TRANSCRIPTION_FAILED`).
- `manual` — never writes `caption_text` (no editing UI exists yet); only records `caption_source = "manual"` so the stage is idempotent and a future UI's caption is never overwritten by this pipeline.
- `none` — no caption.

No `DRAFT`/`APPROVED`/`AUTO` approval state machine — `caption_text` + `caption_source` alone is enough for this milestone; adding review-state now wouldn't simplify anything without a UI to act on it.

### What stayed out of scope, and why

- **No `platform_posts` table.** Video:post stays 1:1 until TikTok publishing actually exists — a `platform_posts` table today would be exactly the "large generalized publishing framework in anticipation of future platforms" this milestone was explicitly told to avoid. When TikTok publishing lands, `content_slots`/`videos` carry everything a first publisher needs (caption, transcript, scheduled_at); a `platform_posts` table (or an equivalent) is that milestone's decision to make with real requirements in hand.
- **No calendar-event title rewrite on assignment.** FIFO events are titled the fixed `"Content Post"` at generation time. Rewriting it to `"Scheduled Video — <filename>"` after `process_content.py` assigns a video would require wiring Calendar/OAuth access into `process_content.py` — new infrastructure this milestone doesn't otherwise need. Deferred follow-up.
- **FIFO ordering durability is scoped to mtime/copy stability, not rename stability.** `process_content.discover_videos()` sorts a video already known to the store (via `content_store.get_video_by_path`) by its immutable `created_at`; a genuinely new path sorts by filesystem mtime. A file renamed between runs has no `original_path` match and is treated as newly discovered, sorting by its current mtime. Hash-based identity (`videos.file_hash`) would close this gap but requires re-hashing every file in `content/incoming/` on every discovery pass just to compute sort order — real I/O cost with no other benefit. Documented limitation, not silently broken behavior.
- **Publishing-readiness state model** (`QUEUED`/`SCHEDULED`/`READY_TO_PUBLISH`/`PUBLISHING`/`PUBLISHED`/`PUBLISH_FAILED`) is a future direction, not built now. This milestone's guardrail: nothing added here treats `ASSIGNED` as more terminal than it already was — a future publishing milestone can still layer new states on top of `content_slots`/`videos` without an incompatible rewrite.
- **Missed-slot policy** (skip / publish immediately / roll to next slot / shift the queue) is unaddressed — future-only generation already guarantees no *new* slot is ever created in the past, and this milestone doesn't add a background scheduler that could miss one. Left for the publishing milestone, once real scheduler behavior is observable.
- **No rolling-month auto-generation.** Month-based `--month`/`--year` generation is preserved unchanged.

## Consequences

- `content-calendar` can now run its full loop — generate a month, drop videos, get them scheduled — with zero AI/classifier dependency and zero risk of a transcription hiccup blocking automation, which was the actual product ask.
- A `videos` row can now be `ASSIGNED` with `transcript IS NULL` (FIFO, transcription failed) — any future code reading `videos.transcript` for caption/analytics purposes must handle `NULL` regardless of `status`, not just for the traditionally-transient early statuses.
- Switching `ROUTING_MODE=pillar` remains a fully supported, regression-tested mode — no pillar/classifier/evaluation infrastructure was removed or altered.
- `clear_calendar.py`'s per-slot output now prints `[fifo]` instead of a pillar key for an untyped slot (was previously guaranteed non-null).

## Guardrails

- Do not let FIFO mode construct or call a `ContentClassifier` under any circumstance — that's the whole point of the cost/failure decoupling.
- Do not let pillar mode's transcription-failure handling change — classification still requires a transcript, so it stays a hard block there.
- Do not move slot assignment before transcription in FIFO mode — see the crash-window reasoning above.
- Do not add per-platform caption truncation to `caption.build_caption_from_transcript` — that belongs at the eventual publisher boundary.
- Do not infer `ROUTING_MODE` from `CONTENT_TYPES`'s presence/emptiness; keep it an explicit, separately validated setting.

## Current Implementation

- `config.py` — `ROUTING_MODE`, `CAPTION_MODE`.
- `scheduling.py` — `validate_routing_mode`, `validate_cadence_config` (extracted from `validate_schedule_config`).
- `caption.py` — new module: `validate_caption_mode`, `build_caption_from_transcript`.
- `content_store.py` — nullable `content_slots.pillar_key` (+ generalized migration), `videos.caption_text`/`caption_source`, `find_earliest_open_slot_fifo`, `get_video_by_path`.
- `slot_matcher.py` — `select_slot_fifo`.
- `generate_calendar.py` — `build_schedule(..., routing_mode=...)` branches; FIFO event body (`"Content Post"`, no `colorId`); mode-aware `print_summary`/`main()` validation.
- `process_content.py` — mode-aware `process_one` (transcribe non-blocking + caption stage + fifo/pillar branch for matching), `discover_videos(store, ...)` durable ordering, `main()` skips classifier construction in FIFO mode.
- `clear_calendar.py` — `pillar_key or 'fifo'` display fix.
- Tests: `tests/test_scheduling.py`, `tests/test_caption.py`, `tests/test_content_store.py`, `tests/test_fifo_scheduling.py`, `tests/test_fifo_process_content.py`, `tests/test_generate_calendar.py`, `tests/test_future_only_schedule.py`, `tests/test_process_content_integration.py` (the last three updated to pass `routing_mode="pillar"` explicitly where they assert pillar-specific behavior, now that `fifo` is the default).
