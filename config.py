"""
config.py — Configuration for the content calendar generator.

Settings include:
  - Content type allocation targets (reference only; actual count follows weekly schedule)
  - Weekly day → content type schedule
  - Sunday alternation rotation
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
# Content allocation targets (informational — real count driven by schedule)
# ---------------------------------------------------------------------------
BUILDING_SYSTEMS_ALLOC = 0.35       # ~10–11 posts per month
ENTREPRENEURSHIP_ALLOC = 0.30       # ~9 posts per month
PERSONAL_TRANSFORM_ALLOC = 0.20     # ~6 posts per month
EDUCATIONAL_ALLOC = 0.15            # ~4–5 posts per month

# ---------------------------------------------------------------------------
# Default run parameters (overridden by CLI flags)
# ---------------------------------------------------------------------------
DEFAULT_MONTH = int(os.getenv("CONTENT_CALENDAR_DEFAULT_MONTH", "6"))
DEFAULT_YEAR = int(os.getenv("CONTENT_CALENDAR_DEFAULT_YEAR", "2026"))

# ---------------------------------------------------------------------------
# Weekly schedule: Python weekday int → content type string
# 0=Monday … 5=Saturday, 6=Sunday handled via SUNDAY_ROTATION below
# ---------------------------------------------------------------------------
WEEKLY_SCHEDULE: dict[int, str] = {
    0: "Entrepreneurship Journey",   # Monday
    1: "Building Systems",           # Tuesday
    2: "Entrepreneurship Journey",   # Wednesday
    3: "Personal Transformation",    # Thursday
    4: "Building Systems",           # Friday
    5: "Entrepreneurship Journey",   # Saturday
}

# Sundays alternate; index 0 is week-1, index 1 is week-2, then repeats
SUNDAY_ROTATION: list[str] = [
    "Educational",           # odd Sundays  (week 1, 3, 5 …)
    "Personal Transformation",  # even Sundays (week 2, 4, 6 …)
]

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
# Google Calendar colorId per content type
# 5=Banana  6=Sage  9=Blueberry  11=Tomato  (per spec)
# ---------------------------------------------------------------------------
COLOR_IDS: dict[str, str] = {
    "Building Systems":       "9",   # blueberry
    "Entrepreneurship Journey": "11", # tomato
    "Personal Transformation": "6",  # sage
    "Educational":            "5",   # banana
}
