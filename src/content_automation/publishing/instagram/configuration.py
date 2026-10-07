"""
configuration.py — the Instagram integration's configuration contract and
its validation (Milestone 4.0; token refresh window added in 4.1).

What it does:
  SCOPES lists the Instagram Login permissions Pickle Batch requests
  (verified against Meta's docs 2026-10-04; the pre-2025 business_* names
  were deprecated on 2025-01-27). configuration_problems() returns
  human-readable problems with the API-side configuration — variable names
  only, never values — so the connect flow (4.1) and startup diagnostics
  can fail clearly instead of sending Meta an unusable request.

Dependencies:
  content_automation.config.
"""

from urllib.parse import urlparse

from content_automation import config

# instagram_business_basic: profile identity, and required to refresh a
# long-lived token. instagram_business_content_publish: create containers
# and media_publish.
SCOPES: tuple[str, ...] = ("instagram_business_basic", "instagram_business_content_publish")

MIN_MEDIA_URL_TTL_SECONDS = 300
MAX_MEDIA_URL_TTL_SECONDS = 86_400

# A long-lived token lives 60 days; a refresh window outside 1–59 days would
# either refresh constantly or never before expiry.
MIN_TOKEN_REFRESH_WINDOW_SECONDS = 86_400
MAX_TOKEN_REFRESH_WINDOW_SECONDS = 59 * 86_400


def configuration_problems() -> list[str]:
    """Empty when the API process has everything the Instagram connect
    flow needs."""
    problems: list[str] = []
    if not config.INSTAGRAM_APP_ID:
        problems.append("INSTAGRAM_APP_ID is not set")
    elif not config.INSTAGRAM_APP_ID.isdigit():
        problems.append("INSTAGRAM_APP_ID must be the numeric Instagram app ID")
    if not config.INSTAGRAM_APP_SECRET:
        problems.append("INSTAGRAM_APP_SECRET is not set")
    redirect = urlparse(config.INSTAGRAM_REDIRECT_URI)
    if not config.INSTAGRAM_REDIRECT_URI:
        problems.append("INSTAGRAM_REDIRECT_URI is not set")
    elif redirect.scheme != "https" or not redirect.netloc:
        problems.append("INSTAGRAM_REDIRECT_URI must be an absolute https URL")
    if not MIN_MEDIA_URL_TTL_SECONDS <= config.INSTAGRAM_MEDIA_URL_TTL_SECONDS <= MAX_MEDIA_URL_TTL_SECONDS:
        problems.append(
            f"INSTAGRAM_MEDIA_URL_TTL_SECONDS must be between {MIN_MEDIA_URL_TTL_SECONDS} and {MAX_MEDIA_URL_TTL_SECONDS}"
        )
    if not MIN_TOKEN_REFRESH_WINDOW_SECONDS <= config.INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS <= MAX_TOKEN_REFRESH_WINDOW_SECONDS:
        problems.append(
            "INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS must be between "
            f"{MIN_TOKEN_REFRESH_WINDOW_SECONDS} and {MAX_TOKEN_REFRESH_WINDOW_SECONDS}"
        )
    return problems


def is_configured() -> bool:
    return not configuration_problems()
