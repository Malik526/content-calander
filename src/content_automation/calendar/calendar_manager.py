"""
calendar_manager.py — Dedicated app-owned Google Calendar: OAuth ownership,
creation/reuse, and persisted identity.

What it does:
  Authenticates as the human account (OAuth, not the shared service account)
  and resolves the one dedicated "Content Automation" calendar that normal
  operation targets — reusing it via a persisted calendar_id if it already
  exists, recovering by display-name search if the state file was lost, or
  creating it if neither works. Never falls back to "primary" or any other
  calendar. See docs/decisions/0004-dedicated-google-calendar-ownership.md
  for why OAuth (not the service account) owns this calendar.

Dependencies:
  google-auth-oauthlib, google-api-python-client
  config.py for calendar identity/OAuth paths
"""

import json
from pathlib import Path

from googleapiclient.errors import HttpError

from content_automation.config import (
    APP_CALENDAR_DESCRIPTION,
    APP_CALENDAR_STATE_PATH,
    APP_CALENDAR_SUMMARY,
    CALENDAR_OAUTH_CLIENT_SECRETS_PATH,
    CALENDAR_OAUTH_SCOPES,
    CALENDAR_OAUTH_TOKEN_PATH,
    TIMEZONE,
)


class CalendarAuthError(Exception):
    """OAuth credentials could not be obtained/refreshed."""


class CalendarStateError(Exception):
    """The dedicated app calendar could not be resolved (and none may be created)."""


def build_oauth_calendar_service():
    """Authenticate as the human account and return a Calendar API client.

    Uses a cached refresh token (CALENDAR_OAUTH_TOKEN_PATH) after the first
    successful run. The first run requires CALENDAR_OAUTH_CLIENT_SECRETS_PATH
    to already exist (one-time download from Google Cloud Console) and opens
    a local browser for one-time interactive consent — see README.md.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials as UserCredentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise CalendarAuthError(
            "google-auth-oauthlib is not installed. Run: pip install google-auth-oauthlib"
        ) from exc
    from googleapiclient.discovery import build

    token_path = Path(CALENDAR_OAUTH_TOKEN_PATH)
    creds = None
    if token_path.exists():
        try:
            creds = UserCredentials.from_authorized_user_file(str(token_path), CALENDAR_OAUTH_SCOPES)
        except (ValueError, OSError):
            creds = None  # corrupt/unreadable token file; fall through to re-auth

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise CalendarAuthError(f"Failed to refresh OAuth credentials: {exc}") from exc
        else:
            secrets_path = Path(CALENDAR_OAUTH_CLIENT_SECRETS_PATH)
            if not secrets_path.exists():
                raise CalendarAuthError(
                    f"No Google OAuth client secrets found at {secrets_path}.\n"
                    "One-time setup: in Google Cloud Console, create an OAuth 2.0 Client ID "
                    "(Application type: Desktop app) on a project with the Calendar API enabled, "
                    "download its JSON, and save it at that path. A browser window will then open "
                    "for one-time consent; after that, credentials are cached and reused."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), CALENDAR_OAUTH_SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as exc:
                raise CalendarAuthError(f"OAuth consent flow failed: {exc}") from exc

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def load_calendar_state() -> dict | None:
    path = Path(APP_CALENDAR_STATE_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_calendar_state(calendar_id: str, summary: str) -> None:
    path = Path(APP_CALENDAR_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"calendar_id": calendar_id, "summary": summary}, indent=2), encoding="utf-8")


def _find_existing_app_calendar(service, summary: str) -> str | None:
    """Defensive recovery if the state file is lost but the calendar still
    exists: search the account's own calendars (accessRole == owner) for a
    matching display name. Not the primary lookup path — display names
    aren't unique, so this only runs when persisted state is absent/stale."""
    page_token = None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for entry in result.get("items", []):
            if entry.get("summary") == summary and entry.get("accessRole") == "owner":
                return entry["id"]
        page_token = result.get("nextPageToken")
        if not page_token:
            return None


def resolve_app_calendar(service, *, create_if_missing: bool = True) -> str:
    """Return the dedicated app calendar's ID.

    Order: persisted state (verified accessible) -> display-name recovery
    search -> create new (only if create_if_missing). Never returns
    "primary" or any calendar not matching this app's identity.

    Raises CalendarStateError if create_if_missing is False and no app
    calendar can be resolved — used by clear_calendar.py's default path,
    which must fail closed rather than create a calendar just to clear it,
    or fall back to any other calendar.
    """
    state = load_calendar_state()
    if state and state.get("calendar_id"):
        try:
            service.calendars().get(calendarId=state["calendar_id"]).execute()
            return state["calendar_id"]
        except HttpError:
            pass  # persisted calendar is gone or access was revoked; try recovery below

    found_id = _find_existing_app_calendar(service, APP_CALENDAR_SUMMARY)
    if found_id:
        save_calendar_state(found_id, APP_CALENDAR_SUMMARY)
        return found_id

    if not create_if_missing:
        raise CalendarStateError(
            f"No dedicated '{APP_CALENDAR_SUMMARY}' calendar found, and none will be created here "
            "(refusing to fall back to any other calendar). Run generate_calendar.py first."
        )

    created = service.calendars().insert(body={
        "summary": APP_CALENDAR_SUMMARY,
        "timeZone": TIMEZONE,
        "description": APP_CALENDAR_DESCRIPTION,
    }).execute()
    calendar_id = created["id"]
    save_calendar_state(calendar_id, APP_CALENDAR_SUMMARY)
    return calendar_id
