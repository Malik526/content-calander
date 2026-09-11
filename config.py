"""
config.py — Configuration for the content calendar generator.

Settings include:
  - Content pillar labels, allocation targets, and Google Calendar colors
  - Weekly day → content pillar schedule
  - Month-end Sunday rebalancing behavior
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
# Updated: July 2026
# Goal: Shift MoreClientsCo toward visible acquisition work while keeping
# systems, execution, and discipline in the weekly mix.
# ---------------------------------------------------------------------------
CONTENT_TYPES: dict[str, dict[str, str | float]] = {
    "acquisition": {
        "label": "Customer Acquisition in Action",
        "color_id": "9",
        "target_percent": 0.40,
        "description": (
            "Outreach and lead generation in action: cold calls, cold emails, "
            "DM outreach, follow-up sequences, booked demos, and the results "
            "and lessons from prospecting activity."
        ),
    },
    "building": {
        "label": "Building Systems & Tools",
        "color_id": "10",
        "target_percent": 0.25,
        "description": (
            "Building or improving internal tools, automation, and the agency's "
            "tech stack: architecture walkthroughs, new features, integrations, "
            "and engineering decisions behind the systems that run the agency."
        ),
    },
    "execution": {
        "label": "Agency Execution",
        "color_id": "5",
        "target_percent": 0.20,
        "description": (
            "Day-to-day agency operating metrics and business execution: "
            "pipeline updates, revenue/MRR, client counts, booking rates, "
            "retention, and weekly wins/losses recaps."
        ),
    },
    "mindset": {
        "label": "Mindset & Discipline",
        "color_id": "3",
        "target_percent": 0.15,
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
# Weekly schedule: day name → content pillar key
# ---------------------------------------------------------------------------
WEEKLY_SCHEDULE: dict[str, str] = {
    "monday": "acquisition",
    "tuesday": "building",
    "wednesday": "acquisition",
    "thursday": "execution",
    "friday": "building",
    "saturday": "acquisition",
    "sunday": "mindset",
}

# Fifth Sundays rebalance the monthly mix toward Agency Execution, which runs
# low in the normal weekly pattern.
FIFTH_SUNDAY_CONTENT_TYPE = "execution"

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
# ---------------------------------------------------------------------------
EVENT_START_HOUR = 9          # 9:00 AM local time
EVENT_DURATION_MINUTES = 30   # 9:00 → 9:30 AM

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
