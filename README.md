# Content Calendar Generator

Generates a full month of short-form video content calendar events and pushes them to Google Calendar automatically (run once at the start of each month), and automates routing recorded videos into that schedule: drop `.mov`/`.mp4` files into `content/incoming/`, run `process_content.py`, and each video is transcribed, classified into a content pillar, and assigned to the earliest matching future posting slot.

See `PROJECT_STATE.md` for current architecture and `docs/decisions/` for why it's built this way.

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

Place `.mov`/`.mp4` files in `content/incoming/` first. A video is only auto-assigned once you have generated a month whose slots are still in the future — see "Video Processing Setup" below.

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

- Set `ANTHROPIC_API_KEY` in `.env` (used for pillar classification via Claude).
- First transcription run downloads the local `faster-whisper` model weights (`base` by default, `CONTENT_CALENDAR_WHISPER_MODEL` to change it) — no API key needed for transcription, it runs fully offline after that.
- `process_content.py` only routes videos into **internally persisted** `content_slots`, written by `generate_calendar.py`. If you already generated upcoming months before this feature existed, re-run `generate_calendar.py` for those months so their slots get persisted — there is no automatic import from existing Google Calendar events.

---

## File Reference

| File | Purpose |
|---|---|
| `generate_calendar.py` | Schedule generation, Google Calendar push, `content_slots` persistence |
| `process_content.py` | Video ingestion orchestrator (discover → inspect → transcribe → classify → route → report) |
| `config.py` | All settings: pillar labels/descriptions, allocations, weekly schedule, auth paths, color IDs, pipeline config |
| `prompts.py` | Every daily short-form video prompt organised by pillar |
| `media.py` | ffprobe inspection, TikTok-compatibility check, audio extraction |
| `transcription.py` | `Transcriber` interface + local `faster-whisper` implementation |
| `classification.py` | `ContentClassifier` interface + Claude implementation |
| `slot_matcher.py` | Deterministic earliest-open-slot selection |
| `content_store.py` | SQLite persistence (`videos`, `content_slots`) |
| `requirements.txt` | Python package dependencies |

Run tests with `python3 -m pytest`.

---

## Customising

**Change allocation percentages, labels, colors, or weekly day assignments:**
Edit `config.py` → `CONTENT_TYPES`, `WEEKLY_SCHEDULE`, and `FIFTH_SUNDAY_CONTENT_TYPE`.

**Add or edit prompts:**
Edit `prompts.py` → the `PROMPTS` dict. Each key must match a content type string
used in `config.py`.

---

## Weekly Posting Schedule

| Day | Content Type |
|---|---|
| Monday | Customer Acquisition in Action |
| Tuesday | Building Systems & Tools |
| Wednesday | Customer Acquisition in Action |
| Thursday | Agency Execution |
| Friday | Building Systems & Tools |
| Saturday | Customer Acquisition in Action |
| Sunday | Mindset & Discipline |

Fifth Sundays are assigned to Agency Execution to rebalance the monthly allocation.

---

## Content Allocation Targets

| Type | Target |
|---|---|
| Customer Acquisition in Action | 40% |
| Building Systems & Tools | 25% |
| Agency Execution | 20% |
| Mindset & Discipline | 15% |

> Actual counts are driven by the weekly schedule above.
> The targets are reference benchmarks only.
