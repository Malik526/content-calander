# Content Calendar Generator

Generates a month of short-form video content calendar events and pushes them to Google Calendar automatically, and automates routing recorded videos into that schedule: drop `.mov`/`.mp4` files into `content/incoming/`, run `process_content.py`, and each video is transcribed, given a caption candidate, and assigned to the earliest matching future posting slot.

**Two routing modes** (`config.ROUTING_MODE`, default **`fifo`**):

- **`fifo`** (default) — no content pillar or classifier required. Videos are scheduled deterministically in ingestion order into untyped posting slots. Transcription still runs (for the transcript/caption and future intelligence), but a transcription failure does not block scheduling — it's recorded and the video is scheduled anyway.
- **`pillar`** — the original strategy: each video is classified into a `config.CONTENT_TYPES` pillar and routed to the earliest open slot for that pillar, with weighted allocation across the month.

See "Routing Mode" below, `PROJECT_STATE.md` for current architecture, and `docs/decisions/` for why it's built this way.

Generation is **future-only** in both modes: run it partway through the current month and it schedules only what's left, not the whole month. See "Run Commands" below.

> **The pillar strategy itself is provisional.** `config.CONTENT_TYPES` is currently set to an engineering-focused pillar set (Software Engineering & Building, Early-Career Software Engineering, Building in Public, Mindset & Discipline) — relevant only when `ROUTING_MODE=pillar`. See "Customising" below and `PROJECT_STATE.md`.

---

## Run Commands

**Generate a month's calendar** — normal operation always targets one dedicated, app-owned "Content Automation" calendar (created on first real run, reused after that — never your primary calendar). Only posting datetimes that haven't already passed are generated: for the current month that means whatever's left from now, not the whole month; for a past month, nothing.

```bash
python3 generate_calendar.py --month [MM] --year [YYYY]
```

**Example — July 2026:**

```bash
python3 generate_calendar.py --month 07 --year 2026
```

If every candidate posting datetime for the requested month has already passed, the command prints `No future posting slots remain for <Month> <Year>.` and exits cleanly — no Calendar or database writes.

**Preview without creating events or touching Google Calendar at all**

```bash
python3 generate_calendar.py --month 07 --year 2026 --dry-run
```

**Clear the app's managed schedule** (only ever touches the dedicated calendar's own tracked events — see Calendar Ownership below):

```bash
python3 clear_calendar.py --dry-run   # report what would be removed
python3 clear_calendar.py             # clear unassigned (OPEN) slots + their events
python3 clear_calendar.py --all       # also clear ASSIGNED slots + their events (destructive; see below)
```

**Advanced/debug: target an explicit calendar by ID directly** (bypasses the dedicated-calendar boundary entirely; opt-in only, uses the shared service account):

```bash
python3 generate_calendar.py --month 07 --year 2026 --calendar your_calendar_id@group.calendar.google.com
python3 clear_calendar.py --calendar your_calendar_id@group.calendar.google.com --start 2026-06-01 --end 2026-07-01
```

**Process incoming videos:**

```bash
python3 process_content.py
python3 process_content.py --dry-run    # classify/transcribe and cache, but never claim a slot or move a file
python3 process_content.py --verbose    # print media/transcript/classification detail per video
```

Place `.mov`/`.mp4` files in `content/incoming/` first. A video is only auto-assigned once you have generated a month whose slots are still in the future — see "Video Processing Setup" below. This runs **fully locally by default** — no Anthropic API key required, and in the default `fifo` routing mode no classifier is constructed at all (see Routing Mode and Classification below).

**Benchmark or calibrate the classifier:**

```bash
python3 evaluate_classifier.py --classifier embeddings
python3 evaluate_classifier.py --classifier claude          # requires ANTHROPIC_API_KEY
python3 evaluate_classifier.py --classifier embeddings --sweep
```

Reads your local labeled dataset in `evaluation/` (see Classification below); never touches `data/content.db`.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

You'll also need `ffmpeg` (which provides `ffprobe`) on your `PATH` for video processing — it's a system binary, not a Python package:

```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt-get install ffmpeg
```

`process_content.py` checks for `ffmpeg`/`ffprobe` before touching any video and fails with this same instruction if they're missing.

### 2. Configure local environment

Copy `.env.example` to `.env` and update values as needed.

```bash
cp .env.example .env
```

The generator reads simple `KEY=value` pairs from `.env` automatically.

### 3. Set up the dedicated calendar's OAuth ownership (required for normal operation)

Normal operation (`generate_calendar.py`/`clear_calendar.py` with no `--calendar` flag) authenticates as **you**, not the shared service account, so the dedicated "Content Automation" calendar it creates is owned by your own Google account — see `docs/decisions/0004-dedicated-google-calendar-ownership.md` for why. One-time setup:

1. In [Google Cloud Console](https://console.cloud.google.com/), on a project with the **Calendar API** enabled, create an **OAuth 2.0 Client ID** (Application type: **Desktop app**).
2. Download its JSON and save it at `~/.config/content-calendar/calendar_oauth_client_secrets.json` (override the path via `CONTENT_CALENDAR_OAUTH_CLIENT_SECRETS` in `.env` if you want it elsewhere).
3. Run `python3 generate_calendar.py --month MM --year YYYY` for real (not `--dry-run`). A browser window opens once for consent; after that, a cached token (`~/.config/content-calendar/calendar_oauth_token.json`) is reused automatically — no browser on later runs.

Without this, real (non-dry-run) generation fails with a clear error naming exactly what's missing — it never silently falls back to the service account or to "primary".

### 4. Service account (only needed for the `--calendar` advanced override)

The shared service account (`~/growth_agency/credentials/service-account.json`, path configurable via `GOOGLE_SERVICE_ACCOUNT_FILE`) is only used when you explicitly pass `--calendar <id>` to either script — normal operation never touches it for Calendar access. If you use the override, share that target calendar with the service account's email (found in the JSON key as `"client_email"`) with "Make changes to events" permission, the same as before this milestone.

### 5. Video processing setup

- Default classifier is fully local (`fastembed` + `BAAI/bge-small-en-v1.5`) — **no API key needed**. First classification run downloads the ~65MB model to `~/.cache/content-calendar/fastembed` (`CONTENT_CALENDAR_EMBEDDING_CACHE_DIR` to change it); every run after that is offline.
- To use Claude instead (for comparison or benchmarking), set `CONTENT_CALENDAR_CLASSIFIER=claude` and `ANTHROPIC_API_KEY` in `.env`.
- First transcription run downloads the local `faster-whisper` model weights (`base` by default, `CONTENT_CALENDAR_WHISPER_MODEL` to change it) — no API key needed for transcription either; it runs fully offline after that.
- `process_content.py` only routes videos into **internally persisted** `content_slots`, written by `generate_calendar.py`. If you already generated upcoming months before this feature existed, re-run `generate_calendar.py` for those months so their slots get persisted — there is no automatic import from existing Google Calendar events.

---

## File Reference

| File | Purpose |
|---|---|
| `generate_calendar.py` | Schedule generation, Google Calendar push, `content_slots` persistence |
| `clear_calendar.py` | Clears the dedicated app calendar's tracked schedule (or an explicit override calendar) |
| `calendar_manager.py` | OAuth auth + create/reuse/persist the dedicated app-owned Google Calendar |
| `scheduling.py` | Posting-date generation, routing-mode validation, weighted pillar allocation (pure functions, no I/O) |
| `process_content.py` | Video ingestion orchestrator — branches on `ROUTING_MODE` (discover → inspect → transcribe → [caption] → [classify, pillar mode only] → route → report) |
| `config.py` | All settings: routing/caption mode, pillar labels/descriptions/weights, posting cadence, auth paths, color IDs, pipeline config |
| `prompts.py` | Every daily short-form video prompt organised by pillar (pillar mode only; see Customising) |
| `media.py` | ffprobe inspection, TikTok-compatibility check, audio extraction |
| `transcription.py` | `Transcriber` interface + local `faster-whisper` implementation |
| `caption.py` | Caption-mode validation + deterministic transcript-to-caption-candidate derivation |
| `classification.py` | `ContentClassifier` interface, `EmbeddingClassifier` (default, local), `ClaudeClassifier` (optional), `build_classifier()` — pillar mode only |
| `evaluate_classifier.py` | Offline benchmark harness for classifiers against a local labeled dataset |
| `slot_matcher.py` | Deterministic earliest-open-slot selection — `select_slot_fifo` (no pillar) and `select_slot` (pillar mode) |
| `content_store.py` | SQLite persistence (`videos`, `content_slots`) |
| `requirements.txt` | Python package dependencies |

Run tests with `python3 -m pytest`.

---

## Routing Mode

`config.ROUTING_MODE` (`CONTENT_CALENDAR_ROUTING_MODE` in `.env`) picks the scheduling strategy. An unrecognized value fails immediately with a clear error — it's never inferred from whether `CONTENT_TYPES` happens to be configured.

- **`fifo`** (default) — `generate_calendar.py` creates untyped `OPEN` slots (`pillar_key = NULL`) straight from the posting cadence, with no weighted pillar allocation at all. `process_content.py` never constructs a classifier in this mode — an invalid/unset `CONTENT_CALENDAR_CLASSIFIER` simply doesn't matter. Every valid, inspected video is eligible for the earliest open slot in ingestion order (`slot_matcher.select_slot_fifo`), regardless of transcript content. Prompts are not generated in this mode (`content_slots.prompt` stays `NULL`) and the Google Calendar event title is the fixed `"Content Post"`.
- **`pillar`** — unchanged classify-then-match strategy: weighted allocation at generation time, classification + confidence gate + pillar-specific slot matching at ingestion time. See "Customising" and "Classification" below.

**Transcription is decoupled from scheduling in `fifo` mode, not in `pillar` mode.** A video still needs to pass basic media validation (a real, readable file with an audio stream) to be scheduled in either mode — that's a precondition, not "intelligence". But if `faster-whisper` itself fails on a video that *did* pass validation: in `fifo` mode the video is still scheduled (`videos.status = "TRANSCRIPTION_FAILED"`, `transcription_status = "FAILED"`, caption falls back to `caption_source = "none"`); in `pillar` mode this is still a hard failure (classification has nothing to classify), same as before this milestone. See `docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md`.

**FIFO ordering** is oldest-first: a video already known to the database (still waiting in `content/incoming/` from a prior run) keeps its original position via its immutable `videos.created_at`, even if the file is later touched or copied — it does **not** survive the file being renamed, which is treated as a new video. A genuinely new file sorts by filesystem mtime.

---

## Captions

`config.CAPTION_MODE` (`CONTENT_CALENDAR_CAPTION_MODE` in `.env`) controls how `videos.caption_text`/`caption_source` get populated — in **both** routing modes:

- **`transcript_auto`** (default) — `caption.build_caption_from_transcript()` normalizes the transcript's whitespace and stores it as the caption candidate (no truncation — platform-specific length limits belong at the future per-platform publisher, not in the canonical stored caption). Falls back to `caption_source = "none"` if transcription never produced a transcript.
- **`manual`** — reserved for a future editing UI; this pipeline never writes `caption_text` in this mode, only records `caption_source = "manual"` so the stage doesn't re-run. An existing manual caption is never overwritten.
- **`none`** — no caption is generated.

---

## Customising

**Posting cadence, days, and time** — edit `config.py`:

```python
POSTS_PER_WEEK = 3                              # 1-7
POSTING_DAYS = ["monday", "wednesday", "friday"] # or "auto" for evenly-spaced weekdays
POSTING_TIME = "10:00"                           # "HH:MM", 24-hour, local (TIMEZONE)
```

`POSTING_DAYS = "auto"` deterministically spreads `POSTS_PER_WEEK` posts across the week (`scheduling.auto_posting_weekdays`) with no randomization; an explicit list always overrides it and must have exactly `POSTS_PER_WEEK` distinct weekday names.

**Future-only generation:** candidate posting datetimes before "now" (in `TIMEZONE`) are discarded *before* pillar weights are allocated — so running `generate_calendar.py` for the current month allocates the configured percentages across whatever's actually left, not the full month. A same-day slot is still generated if its `POSTING_TIME` hasn't passed yet. `build_schedule(year, month, start_at=...)` accepts an explicit boundary (mainly for tests); omitted, it resolves the real current time via the same helper `slot_matcher.py` uses for slot matching, so there's one single definition of "now" across the whole app.

**Content pillars and their share of the schedule** (`ROUTING_MODE=pillar` only) — edit `config.py` → `CONTENT_TYPES`. Any number of pillars is supported; each needs a `label`, `color_id`, `description` (used by the classifier), and `weight`. Weights must sum to `1.0`:

```python
CONTENT_TYPES = {
    "engineering": {"label": "Software Engineering & Building", "color_id": "10", "weight": 0.40, "description": "..."},
    "career": {"label": "Early-Career Software Engineering", "color_id": "9", "weight": 0.30, "description": "..."},
    "building_in_public": {"label": "Building in Public", "color_id": "5", "weight": 0.20, "description": "..."},
    "mindset": {"label": "Mindset & Discipline", "color_id": "3", "weight": 0.10, "description": "..."},
}
```

Monthly counts are computed from real calendar dates and the largest-remainder method (`scheduling.allocate_pillars`), then interleaved across the month (`scheduling.distribute_pillars`) rather than clustered — see `docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md`. Invalid configuration (weights not summing to 1.0, a bad posting time, mismatched posting-day count, etc.) fails clearly at startup rather than silently normalizing.

**Prompts** — edit `prompts.py` → the `PROMPTS` dict; each key must match a pillar key in `CONTENT_TYPES`. Prompts are optional: set `PROMPT_GENERATION_ENABLED = False` in `config.py` to generate a schedule with no prompt text at all (`content_slots.prompt` will be `NULL`). Either way, prompts never affect which date or pillar a slot gets. (Current prompt lists for `engineering`/`career`/`building_in_public` are minimal placeholders to keep generation functional during pillar testing, not a designed content plan — see `prompts.py`'s module docstring.)

> Changing the strategy and re-running `generate_calendar.py` for a month that already has persisted slots only *adds* slots for newly-covered dates — it never rewrites an existing slot's pillar or prompt. Mixing two strategies within one already-generated month is a known limitation; regenerate the whole month fresh (see ADR-0002) if you need a clean re-strategize.

---

## Classification

Only relevant when `ROUTING_MODE=pillar` — in `fifo` mode (the default) no classifier is constructed or called at all. `config.CLASSIFIER` selects which `ContentClassifier` runs (`config.py`, or `CONTENT_CALENDAR_CLASSIFIER` in `.env`):

- **`embeddings`** (default) — fully local, no API key. Compares a transcript's embedding to each pillar's semantic profile (`label` + `description` + `classification_examples`, from `CONTENT_TYPES`) via cosine similarity, using `fastembed` + `BAAI/bge-small-en-v1.5`. Auto-assigns only if the top pillar clears **both** `EMBEDDING_MIN_SIMILARITY` and `EMBEDDING_MIN_MARGIN` (margin over the second-best pillar) — otherwise the video goes to `NEEDS_REVIEW`. These two thresholds ship as **explicitly uncalibrated placeholders**; see below for calibrating them.
- **`claude`** — the Anthropic implementation from Milestone 1, requires `ANTHROPIC_API_KEY`. Useful as a stronger reference/benchmark, not required for normal operation.

Add representative examples per pillar in `config.py` to improve embedding accuracy:

```python
CONTENT_TYPES = {
    "engineering": {
        "label": "Software Engineering & Building",
        "description": "...",
        "weight": 0.40,
        "classification_examples": [
            "Explaining how a software tool was architected.",
            "Demonstrating an automation or API integration.",
        ],
    },
    ...
}
```

**Calibrating the thresholds:** build a labeled dataset of your own real transcripts in `evaluation/` (see `evaluation/README.md` for the exact format — it's gitignored, your transcripts never enter source control), then:

```bash
python3 evaluate_classifier.py --classifier embeddings --sweep
```

This reports auto-assigned count, **wrong auto-assignments**, review count, and accuracy for a grid of similarity/margin combinations, without re-embedding per combination. Pick the combination with the lowest wrong-auto-assignment rate you're comfortable with, then set `EMBEDDING_MIN_SIMILARITY`/`EMBEDDING_MIN_MARGIN` accordingly. `python3 evaluate_classifier.py --classifier embeddings` (no `--sweep`) runs a normal report against the currently configured thresholds; add `--classifier claude` to compare against Claude on the exact same dataset.

`ClassificationResult.confidence` is a raw cosine similarity for `embeddings`, not a calibrated probability — the CLI labels it "Similarity score" rather than "Confidence" for anything but Claude. See `docs/decisions/0003-local-embedding-classification.md`.

---

## Shofo Real-Video Evaluation Corpus

A separate, optional test/evaluation utility (Milestone 1.3.1) for exercising this pipeline against real short-form social video instead of only synthetic fixtures. It never touches production scheduling behavior.

```bash
pip install -r requirements-eval.txt          # kept out of requirements.txt on purpose
python3 download_shofo_samples.py --count 12  # ~12 real MP4s + reference metadata, not the full dataset
python3 evaluate_transcription.py             # faster-whisper vs. the dataset's reference transcript (WER/CER)
```

Notes:

- Pulls from the gated [`Shofo/shofo-talking-head-en`](https://huggingface.co/datasets/Shofo/shofo-talking-head-en) dataset (~10k clips, ~104GB) — this never downloads the full dataset, only ~12 selected raw MP4s via `huggingface_hub.hf_hub_download`, and never decodes video during selection.
- Dataset access requires accepting Hugging Face's access conditions for that dataset and authenticating locally (`huggingface-cli login` or `HF_TOKEN`) — never a token in source code.
- Downloaded videos, metadata, and results are gitignored and never committed — see `evaluation/video_pipeline/README.md`.
- The dataset's supplied transcript is a **reference** ASR output (from another model), not human ground truth — `evaluate_transcription.py` reports WER/CER differences, it does not assume every mismatch is faster-whisper's fault.
- Tests media/transcription/FIFO-scheduling — **not** pillar-classification accuracy; the clips' topics are unrelated to this project's content pillars.

See `evaluation/video_pipeline/README.md` for the full workflow, including copying a subset into `content/incoming/` for a real FIFO pipeline test.

## Calendar Ownership

Normal operation targets exactly one dedicated, app-owned Google Calendar (display name **"Content Automation"**) — never your primary calendar, never an arbitrary calendar. See `docs/decisions/0004-dedicated-google-calendar-ownership.md` for the full rationale.

- **Created once, reused after that.** `calendar_manager.resolve_app_calendar()` reuses the calendar ID persisted in `data/calendar_state.json`; if that calendar is gone or access was revoked, it recovers by searching your own calendars for a name match before ever creating a new one.
- **Owned by you, not the service account.** Created/managed via OAuth (see Setup step 3) so it shows up directly in your own Google Calendar, with no sharing step required.
- **`clear_calendar.py` only clears what the app tracks.** Default scope is `content_slots` rows in `OPEN` status and their exact calendar events — not a calendar-wide date-range wipe. `--all` also clears `ASSIGNED` slots (and resets those videos back to `CLASSIFIED` so they can be rescheduled — their transcript/classification history is untouched). The calendar itself is never deleted by either mode.
- **`--calendar <id>` is an explicit, opt-in override** on both scripts, using the shared service account exactly as this tool worked before this milestone — useful for advanced/debug targeting of a specific calendar, never the default.
- **Fails closed.** If the dedicated calendar can't be resolved (missing OAuth setup, or `clear_calendar.py` finding no app calendar at all) the command exits with a clear error — it never silently falls back to any other calendar.
