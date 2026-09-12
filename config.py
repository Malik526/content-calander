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
# Updated: September 2026 — replaced the agency-oriented pillar set
# (acquisition/building/execution/mindset) with a provisional
# engineering-focused set for testing classification/routing against the
# current content strategy. See PROJECT_STATE.md ("Active Pillar Strategy")
# and the 2026-09-12 CHANGELOG.md entry.
# Weights must sum to 1.0 (validated by scheduling.validate_pillar_weights).
# ---------------------------------------------------------------------------
CONTENT_TYPES: dict[str, dict[str, str | float | list[str]]] = {
    "engineering": {
        "label": "Software Engineering & Building",
        "color_id": "10",
        "weight": 0.40,
        "description": (
            "Software engineering, technical projects, AI agents, automation, "
            "system architecture, APIs, debugging, implementation decisions, "
            "and lessons learned while building software."
        ),
        "classification_examples": [
            "Explaining how a software system or AI agent was architected.",
            "Walking through a technical decision made while building a project.",
            "Discussing an API integration, automation, or engineering workflow.",
            "Sharing a bug, performance problem, or implementation lesson.",
        ],
    },
    "career": {
        "label": "Early-Career Software Engineering",
        "color_id": "9",
        "weight": 0.30,
        "description": (
            "The process of becoming a stronger early-career software engineer "
            "and breaking into startup engineering roles: job searching, "
            "interviews, skill development, career decisions, and lessons from "
            "trying to enter the software industry."
        ),
        "classification_examples": [
            "Discussing how to land a first software engineering or startup role.",
            "Reflecting on an engineering interview or job application.",
            "Explaining what an early-career engineer should learn.",
            "Talking about career strategy, resumes, portfolios, or engineering experience.",
        ],
    },
    "building_in_public": {
        "label": "Building in Public",
        "color_id": "5",
        "weight": 0.20,
        "description": (
            "Documenting the real process of building projects and developing "
            "as an engineer: progress updates, experiments, failures, changes "
            "in direction, lessons learned, and what is currently being worked on."
        ),
        "classification_examples": [
            "Sharing progress on a software project currently being built.",
            "Talking about what changed in a project this week.",
            "Reflecting on an experiment that succeeded or failed.",
            "Documenting the process of learning or building something publicly.",
        ],
    },
    "mindset": {
        "label": "Mindset & Discipline",
        "color_id": "3",
        "weight": 0.10,
        "description": (
            "Personal development, discipline, consistency, resilience, and "
            "reflection related to pursuing engineering, building projects, "
            "learning, and long-term career growth."
        ),
        "classification_examples": [
            "Reflecting on discipline or consistency while learning engineering.",
            "Talking about dealing with rejection or setbacks.",
            "Discussing persistence while building a difficult project.",
            "Sharing a personal lesson about growth, courage, or long-term effort.",
        ],
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
POSTS_PER_WEEK = 4

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

# Legacy/advanced-override calendar ID. No longer used as an implicit
# default anywhere — --calendar (generate_calendar.py, clear_calendar.py)
# defaults to None so the dedicated app calendar (below) is always the
# normal-operation target. Kept only so an explicit --calendar <id> without
# a value has something sane to reference; never auto-applied. See
# docs/decisions/0004-dedicated-google-calendar-ownership.md.
DEFAULT_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Timezone string used for all events
TIMEZONE = os.getenv("CONTENT_CALENDAR_TIMEZONE", "America/New_York")

# ---------------------------------------------------------------------------
# Dedicated app-owned Google Calendar
# Added: September 2026. Normal operation (no --calendar override) always
# targets this calendar, created/owned via user OAuth (not the shared
# service account) so a human owns it, per Google's guidance for apps that
# create secondary calendars. See
# docs/decisions/0004-dedicated-google-calendar-ownership.md.
# ---------------------------------------------------------------------------

# Display name/description for the calendar the app creates on first real
# (non-dry-run) generation, and reuses on every run after that.
APP_CALENDAR_SUMMARY = os.getenv("CONTENT_CALENDAR_APP_CALENDAR_SUMMARY", "Content Automation")
APP_CALENDAR_DESCRIPTION = (
    "Managed by content-calendar (growth_agency/internal-tools/content-calendar). "
    "Events here are created/cleared by the app — avoid adding unrelated events."
)

# Persisted identity of the dedicated calendar (calendar_id + summary), so
# it is reused rather than recreated on every run. Not a display-name
# lookup — display names are not unique.
APP_CALENDAR_STATE_PATH = Path(
    os.getenv(
        "CONTENT_CALENDAR_APP_CALENDAR_STATE_PATH",
        str(Path(__file__).with_name("data") / "calendar_state.json"),
    )
).expanduser()

# OAuth (not the service account) is used specifically for creating/owning
# the dedicated calendar and writing/clearing its events, so a human account
# owns it. google-auth-oauthlib's InstalledAppFlow caches a refresh token
# after one interactive browser consent, so only the very first real run
# needs a browser. CALENDAR_OAUTH_CLIENT_SECRETS_PATH must be downloaded
# once from Google Cloud Console (OAuth 2.0 Client ID, Desktop app type, on
# a project with the Calendar API enabled) — see README.md setup.
CALENDAR_OAUTH_SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_OAUTH_CLIENT_SECRETS_PATH = Path(
    os.getenv(
        "CONTENT_CALENDAR_OAUTH_CLIENT_SECRETS",
        str(Path.home() / ".config" / "content-calendar" / "calendar_oauth_client_secrets.json"),
    )
).expanduser()
CALENDAR_OAUTH_TOKEN_PATH = Path(
    os.getenv(
        "CONTENT_CALENDAR_OAUTH_TOKEN_PATH",
        str(Path.home() / ".config" / "content-calendar" / "calendar_oauth_token.json"),
    )
).expanduser()

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

# Claude-classifier-specific confidence threshold for automatic slot routing
# (ClaudeClassifier applies this itself before returning a result). Below
# this, ClaudeClassifier reports pillar=None and process_content.py treats it
# as NEEDS_REVIEW. EmbeddingClassifier uses its own two-gate policy instead
# (EMBEDDING_MIN_SIMILARITY / EMBEDDING_MIN_MARGIN below) — see
# docs/decisions/0003-local-embedding-classification.md.
AUTO_ASSIGN_THRESHOLD = float(os.getenv("CONTENT_CALENDAR_AUTO_ASSIGN_THRESHOLD", "0.80"))

# --- Transcription (classification.transcription.FasterWhisperTranscriber) ---
WHISPER_MODEL_SIZE = os.getenv("CONTENT_CALENDAR_WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("CONTENT_CALENDAR_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("CONTENT_CALENDAR_WHISPER_COMPUTE_TYPE", "int8")

# --- Classification ---
# Added: September 2026 — local embedding classification, with Claude kept as
# an optional comparison/reference implementation. See
# docs/decisions/0003-local-embedding-classification.md.
# Valid values: "embeddings" (default, fully local, no API key) or "claude".
CLASSIFIER = os.getenv("CONTENT_CALENDAR_CLASSIFIER", "embeddings")

# classification.ClaudeClassifier
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CONTENT_CALENDAR_CLAUDE_MODEL", "claude-sonnet-5")

# classification.EmbeddingClassifier — local, CPU-only sentence embeddings via
# fastembed (ONNX runtime backend; no torch, no CUDA). Default model is
# BAAI/bge-small-en-v1.5: 384-dim, ~65MB quantized ONNX weights, downloaded
# once to EMBEDDING_CACHE_DIR on first use (a few seconds), then runs fully
# offline. CPU-only; fastembed does not require a GPU.
EMBEDDING_MODEL = os.getenv("CONTENT_CALENDAR_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_CACHE_DIR = Path(
    os.getenv("CONTENT_CALENDAR_EMBEDDING_CACHE_DIR", str(Path.home() / ".cache" / "content-calendar" / "fastembed"))
).expanduser()

# Two-gate auto-assign policy for EmbeddingClassifier: the top pillar's cosine
# similarity must clear EMBEDDING_MIN_SIMILARITY, AND its margin over the
# second-best pillar must clear EMBEDDING_MIN_MARGIN. Below either gate, the
# video is marked NEEDS_REVIEW rather than auto-assigned.
#
# THESE ARE UNCALIBRATED PLACEHOLDER DEFAULTS, not production-quality values —
# use evaluate_classifier.py --sweep against a real labeled transcript corpus
# to pick real thresholds before relying on this for unattended scheduling.
EMBEDDING_MIN_SIMILARITY = float(os.getenv("CONTENT_CALENDAR_EMBEDDING_MIN_SIMILARITY", "0.50"))
EMBEDDING_MIN_MARGIN = float(os.getenv("CONTENT_CALENDAR_EMBEDDING_MIN_MARGIN", "0.03"))

# --- TikTok generic compatibility targets (informational in V1; no publishing) ---
# https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
TIKTOK_CONTAINERS = {"mov", "mp4", "webm"}
TIKTOK_VIDEO_CODECS = {"h264", "hevc", "vp8", "vp9"}

# ---------------------------------------------------------------------------
