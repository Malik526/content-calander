# Content Calendar Generator

Generates a full month of short-form video content calendar events and pushes them to Google Calendar automatically (run once at the start of each month), and automates routing recorded videos into that schedule: drop `.mov`/`.mp4` files into `content/incoming/`, run `process_content.py`, and each video is transcribed, classified into a content pillar, and assigned to the earliest matching future posting slot.

See `PROJECT_STATE.md` for current architecture and `docs/decisions/` for why it's built this way.

> **Current pillar strategy is provisional.** `config.CONTENT_TYPES` is currently set to an engineering-focused pillar set (Software Engineering & Building, Early-Career Software Engineering, Building in Public, Mindset & Discipline) for testing classification/routing against the current content direction — not a finalized long-term strategy. See "Customising" below and `PROJECT_STATE.md`.

---

## Run Commands

**Generate a month's calendar:**

```bash
python3 generate_calendar.py --month [MM] --year [YYYY]
```

**Example — July 2026:**

```bash
python3 generate_calendar.py --month 07 --year 2026
```

**Optional: target a specific calendar by ID**

```bash
python3 generate_calendar.py --month 07 --year 2026 --calendar your_calendar_id@group.calendar.google.com
```

**Preview without creating events**

```bash
python3 generate_calendar.py --month 07 --year 2026 --dry-run
```

**Process incoming videos:**

```bash
python3 process_content.py
python3 process_content.py --dry-run    # classify/transcribe and cache, but never claim a slot or move a file
python3 process_content.py --verbose    # print media/transcript/classification detail per video
```

Place `.mov`/`.mp4` files in `content/incoming/` first. A video is only auto-assigned once you have generated a month whose slots are still in the future — see "Video Processing Setup" below. This runs **fully locally by default** — no Anthropic API key required (see Classification below).

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

### 3. Add your service account key

The script authenticates via a Google service account JSON key.
Default expected path: `~/growth_agency/credentials/service-account.json`

To use a different path, update `GOOGLE_SERVICE_ACCOUNT_FILE` in `.env`.

### 4. Grant calendar access to the service account

In Google Calendar settings → "Share with specific people", add the service
account email (found in the JSON key as `"client_email"`) with
"Make changes to events" permission.

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
| `scheduling.py` | Posting-date generation and weighted pillar allocation (pure functions, no I/O) |
| `process_content.py` | Video ingestion orchestrator (discover → inspect → transcribe → classify → route → report) |
| `config.py` | All settings: pillar labels/descriptions/weights, posting cadence, auth paths, color IDs, pipeline config |
| `prompts.py` | Every daily short-form video prompt organised by pillar (optional; see Customising) |
| `media.py` | ffprobe inspection, TikTok-compatibility check, audio extraction |
| `transcription.py` | `Transcriber` interface + local `faster-whisper` implementation |
| `classification.py` | `ContentClassifier` interface, `EmbeddingClassifier` (default, local), `ClaudeClassifier` (optional), `build_classifier()` |
| `evaluate_classifier.py` | Offline benchmark harness for classifiers against a local labeled dataset |
| `slot_matcher.py` | Deterministic earliest-open-slot selection |
| `content_store.py` | SQLite persistence (`videos`, `content_slots`) |
| `requirements.txt` | Python package dependencies |

Run tests with `python3 -m pytest`.

---

## Customising

**Posting cadence, days, and time** — edit `config.py`:

```python
POSTS_PER_WEEK = 3                              # 1-7
POSTING_DAYS = ["monday", "wednesday", "friday"] # or "auto" for evenly-spaced weekdays
POSTING_TIME = "10:00"                           # "HH:MM", 24-hour, local (TIMEZONE)
```

`POSTING_DAYS = "auto"` deterministically spreads `POSTS_PER_WEEK` posts across the week (`scheduling.auto_posting_weekdays`) with no randomization; an explicit list always overrides it and must have exactly `POSTS_PER_WEEK` distinct weekday names.

**Content pillars and their share of the schedule** — edit `config.py` → `CONTENT_TYPES`. Any number of pillars is supported; each needs a `label`, `color_id`, `description` (used by the classifier), and `weight`. Weights must sum to `1.0`:

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

`config.CLASSIFIER` selects which `ContentClassifier` runs (`config.py`, or `CONTENT_CALENDAR_CLASSIFIER` in `.env`):

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
