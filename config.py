"""
config.py — Configuration for the content calendar generator.

Settings include:
  - Content pillar labels, weights, and Google Calendar colors
  - Posting cadence, posting-day strategy, and posting time
  - Google Calendar auth + API settings
  - Event timing and color IDs per content type

No external dependencies.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Local environment file
# ---------------------------------------------------------------------------
# Load simple KEY=value pairs from .env so local tool settings work without
# requiring shell exports or an extra python-dotenv dependency.
ENV_FILE = Path(__file__).with_name(".env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# ---------------------------------------------------------------------------
# Content pillar definitions
# Updated: September 2026 — "target_percent" renamed to "weight" and the
# weekday-mapping model retired in favor of scheduling.allocate_pillars().
# See docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md.
# Weights must sum to 1.0 (validated by scheduling.validate_pillar_weights).
# ---------------------------------------------------------------------------
CONTENT_TYPES: dict[str, dict[str, str | float]] = {
    "acquisition": {
        "label": "Customer Acquisition in Action",
        "color_id": "9",
        "weight": 0.40,
        "description": (
            "Outreach and lead generation in action: cold calls, cold emails, "
            "DM outreach, follow-up sequences, booked demos, and the results "
            "and lessons from prospecting activity."
        ),
    },
    "building": {
        "label": "Building Systems & Tools",
        "color_id": "10",
        "weight": 0.25,
        "description": (
            "Building or improving internal tools, automation, and the agency's "
            "tech stack: architecture walkthroughs, new features, integrations, "
            "and engineering decisions behind the systems that run the agency."
        ),
    },
    "execution": {
        "label": "Agency Execution",
        "color_id": "5",
        "weight": 0.20,
        "description": (
            "Day-to-day agency operating metrics and business execution: "
            "pipeline updates, revenue/MRR, client counts, booking rates, "
            "retention, and weekly wins/losses recaps."
        ),
    },
    "mindset": {
        "label": "Mindset & Discipline",
        "color_id": "3",
        "weight": 0.15,
        "description": (
            "Personal mindset, discipline, and reflection: handling rejection, "
            "consistency over intensity, personal history and lessons applied "
            "to building the agency, not business metrics."
        ),
    },
}

# ---------------------------------------------------------------------------
# Default run parameters (overridden by CLI flags)
# ---------------------------------------------------------------------------
DEFAULT_MONTH = int(os.getenv("CONTENT_CALENDAR_DEFAULT_MONTH", "6"))
DEFAULT_YEAR = int(os.getenv("CONTENT_CALENDAR_DEFAULT_YEAR", "2026"))

# ---------------------------------------------------------------------------
# Posting cadence and pillar allocation strategy
# Replaces the old fixed WEEKLY_SCHEDULE / FIFTH_SUNDAY_CONTENT_TYPE mapping,
# which coupled "when to post" to "what pillar" through weekday alone.
# See scheduling.py and docs/decisions/0002-configurable-cadence-and-weighted-pillar-allocation.md.
# ---------------------------------------------------------------------------

# Posts per week, 1-7. The month's actual slot count is derived from real
# calendar dates (scheduling.generate_posting_dates), not posts_per_week * 4.
POSTS_PER_WEEK = 7

# "auto" (scheduling.auto_posting_weekdays picks evenly-spaced weekdays) or an
# explicit list of exactly POSTS_PER_WEEK distinct weekday names, e.g.
# ["monday", "wednesday", "friday"]. Explicit days always override auto.
POSTING_DAYS: str | list[str] = "auto"

# "HH:MM" 24-hour local time (in TIMEZONE) applied to every generated slot.
POSTING_TIME = "09:00"

# Whether generated content_slots get a prompt attached from PROMPTS
# (rotated per pillar, same rule as before: sequential, wraps only after
# every prompt in the list has been used once). When False, every
# content_slot.prompt is None. Either way, scheduling and slot matching
# never depend on prompt text — see slot_matcher.py.
PROMPT_GENERATION_ENABLED = True

# ---------------------------------------------------------------------------
# Google Calendar API
# ---------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Path to service account JSON key — expand ~ at runtime
SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "~/growth_agency/credentials/service-account.json",
)

# Calendar to write events to; overrideable via --calendar flag
DEFAULT_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Timezone string used for all events
TIMEZONE = os.getenv("CONTENT_CALENDAR_TIMEZONE", "America/New_York")

# ---------------------------------------------------------------------------
# Event timing
# Start time now comes from POSTING_TIME above; this only sets event length.
# ---------------------------------------------------------------------------
EVENT_DURATION_MINUTES = 30

# ---------------------------------------------------------------------------
# Content processing pipeline (process_content.py)
# Added: September 2026 — video ingestion, transcription, classification,
# and slot routing. See docs/decisions/0001-video-ingestion-pipeline.md.
# ---------------------------------------------------------------------------

# SQLite database holding videos and content_slots records.
DB_PATH = Path(
    os.getenv(
        "CONTENT_CALENDAR_DB_PATH",
        str(Path(__file__).with_name("data") / "content.db"),
    )
).expanduser()

# File lifecycle directories for process_content.py.
CONTENT_DIR = Path(__file__).with_name("content")
INCOMING_DIR = CONTENT_DIR / "incoming"
PROCESSED_DIR = CONTENT_DIR / "processed"
FAILED_DIR = CONTENT_DIR / "failed"

# Video file extensions process_content.py will discover in INCOMING_DIR.
SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4"}

# Classification confidence required for automatic slot routing.
# Below this, a video is marked NEEDS_REVIEW instead of being assigned.
AUTO_ASSIGN_THRESHOLD = float(os.getenv("CONTENT_CALENDAR_AUTO_ASSIGN_THRESHOLD", "0.80"))

# --- Transcription (classification.transcription.FasterWhisperTranscriber) ---
WHISPER_MODEL_SIZE = os.getenv("CONTENT_CALENDAR_WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("CONTENT_CALENDAR_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("CONTENT_CALENDAR_WHISPER_COMPUTE_TYPE", "int8")

# --- Classification (classification.ClaudeClassifier) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CONTENT_CALENDAR_CLAUDE_MODEL", "claude-sonnet-5")

# --- TikTok generic compatibility targets (informational in V1; no publishing) ---
# https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
TIKTOK_CONTAINERS = {"mov", "mp4", "webm"}
TIKTOK_VIDEO_CODECS = {"h264", "hevc", "vp8", "vp9"}

# ---------------------------------------------------------------------------
