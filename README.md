# Content Calendar Generator

Generates a month of short-form video content calendar events and pushes them to Google Calendar automatically, and automates routing recorded videos into that schedule: drop `.mov`/`.mp4` files into `content/incoming/`, run `process_content.py`, and each video is transcribed, classified into a content pillar, and assigned to the earliest matching future posting slot.

Generation is **future-only**: run it partway through the current month and it schedules only what's left, not the whole month. See "Run Commands" below and `PROJECT_STATE.md`.

See `PROJECT_STATE.md` for current architecture and `docs/decisions/` for why it's built this way.

> **Current pillar strategy is provisional.** `config.CONTENT_TYPES` is currently set to an engineering-focused pillar set (Software Engineering & Building, Early-Career Software Engineering, Building in Public, Mindset & Discipline) for testing classification/routing against the current content direction — not a finalized long-term strategy. See "Customising" below and `PROJECT_STATE.md`.

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

**Future-only generation:** candidate posting datetimes before "now" (in `TIMEZONE`) are discarded *before* pillar weights are allocated — so running `generate_calendar.py` for the current month allocates the configured percentages across whatever's actually left, not the full month. A same-day slot is still generated if its `POSTING_TIME` hasn't passed yet. `build_schedule(year, month, start_at=...)` accepts an explicit boundary (mainly for tests); omitted, it resolves the real current time via the same helper `slot_matcher.py` uses for slot matching, so there's one single definition of "now" across the whole app.

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

---

## Calendar Ownership

Normal operation targets exactly one dedicated, app-owned Google Calendar (display name **"Content Automation"**) — never your primary calendar, never an arbitrary calendar. See `docs/decisions/0004-dedicated-google-calendar-ownership.md` for the full rationale.

- **Created once, reused after that.** `calendar_manager.resolve_app_calendar()` reuses the calendar ID persisted in `data/calendar_state.json`; if that calendar is gone or access was revoked, it recovers by searching your own calendars for a name match before ever creating a new one.
- **Owned by you, not the service account.** Created/managed via OAuth (see Setup step 3) so it shows up directly in your own Google Calendar, with no sharing step required.
- **`clear_calendar.py` only clears what the app tracks.** Default scope is `content_slots` rows in `OPEN` status and their exact calendar events — not a calendar-wide date-range wipe. `--all` also clears `ASSIGNED` slots (and resets those videos back to `CLASSIFIED` so they can be rescheduled — their transcript/classification history is untouched). The calendar itself is never deleted by either mode.
- **`--calendar <id>` is an explicit, opt-in override** on both scripts, using the shared service account exactly as this tool worked before this milestone — useful for advanced/debug targeting of a specific calendar, never the default.
- **Fails closed.** If the dedicated calendar can't be resolved (missing OAuth setup, or `clear_calendar.py` finding no app calendar at all) the command exits with a clear error — it never silently falls back to any other calendar.
