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
CONTENT_TYPES: dict[str, dict[str, str | float | list[str]]] = {
    "acquisition": {
        "label": "Customer Acquisition in Action",
        "color_id": "9",
        "weight": 0.40,
        "description": (
            "Outreach and lead generation in action: cold calls, cold emails, "
            "DM outreach, follow-up sequences, booked demos, and the results "
            "and lessons from prospecting activity."
        ),
        "classification_examples": [
            "Discussing cold outreach results and reply rates.",
            "Explaining a prospecting or lead-generation experiment.",
            "Breaking down a sales conversation or cold call.",
            "Recapping demos booked or deals closed from outreach.",
        ],
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
        "classification_examples": [
            "Explaining how a software tool or pipeline was architected.",
            "Demonstrating an automation or API integration that was built.",
            "Discussing a technical problem encountered while building a product.",
            "Comparing engineering approaches used in a system.",
        ],
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
        "classification_examples": [
            "Recapping this week's business metrics or pipeline numbers.",
            "Discussing revenue, client count, or retention rate.",
            "Reflecting on a weekly win/loss or a lost deal.",
            "Explaining a change in strategy based on business results.",
        ],
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
        "classification_examples": [
            "Reflecting on discipline, consistency, or motivation.",
            "Discussing how a personal setback or rejection was handled.",
            "Drawing a life lesson from personal history and applying it to work.",
            "Personal reflection that is not about business metrics or tools.",
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
