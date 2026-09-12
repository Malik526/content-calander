"""Tests for calendar_manager.py: dedicated app calendar resolution/creation,
with all Google API calls mocked. No live Google credentials required."""

from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

import calendar_manager


def _http_error(status: int) -> HttpError:
    resp = MagicMock(status=status)
    return HttpError(resp, b'{"error": {"message": "not found"}}')


@pytest.fixture(autouse=True)
def state_path(tmp_path, monkeypatch):
    """Every test gets its own isolated calendar_state.json."""
    path = tmp_path / "calendar_state.json"
    monkeypatch.setattr(calendar_manager, "APP_CALENDAR_STATE_PATH", path)
    return path


def test_existing_dedicated_calendar_is_reused(state_path):
    calendar_manager.save_calendar_state("cal-123", calendar_manager.APP_CALENDAR_SUMMARY)
    service = MagicMock()
    service.calendars().get.return_value.execute.return_value = {"id": "cal-123"}

    result = calendar_manager.resolve_app_calendar(service)

    assert result == "cal-123"
    service.calendars().insert.assert_not_called()


def test_missing_calendar_triggers_creation(state_path):
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {"items": []}
    service.calendars().insert.return_value.execute.return_value = {"id": "new-cal-id"}

    result = calendar_manager.resolve_app_calendar(service)

    assert result == "new-cal-id"
    service.calendars().insert.assert_called_once()
    body = service.calendars().insert.call_args.kwargs["body"]
    assert body["summary"] == calendar_manager.APP_CALENDAR_SUMMARY
    assert body["timeZone"]


def test_created_calendar_id_is_persisted_and_reused_next_time(state_path):
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {"items": []}
    service.calendars().insert.return_value.execute.return_value = {"id": "new-cal-id"}

    calendar_manager.resolve_app_calendar(service)
    assert calendar_manager.load_calendar_state() == {
        "calendar_id": "new-cal-id",
        "summary": calendar_manager.APP_CALENDAR_SUMMARY,
    }

    # Second resolve, fresh service double: must not create again.
    service2 = MagicMock()
    service2.calendars().get.return_value.execute.return_value = {"id": "new-cal-id"}
    result = calendar_manager.resolve_app_calendar(service2)

    assert result == "new-cal-id"
    service2.calendars().insert.assert_not_called()


def test_calendar_never_created_twice_across_repeated_calls(state_path):
    """Do not create a new dedicated calendar on every run."""
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {"items": []}
    service.calendars().insert.return_value.execute.return_value = {"id": "cal-1"}

    first = calendar_manager.resolve_app_calendar(service)

    service.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    second = calendar_manager.resolve_app_calendar(service)

    assert first == second == "cal-1"
    assert service.calendars().insert.call_count == 1


def test_inaccessible_stored_calendar_recovers_via_search(state_path):
    """Deleted/revoked persisted calendar is detected cleanly and recovered,
    not a cryptic failure."""
    calendar_manager.save_calendar_state("stale-id", calendar_manager.APP_CALENDAR_SUMMARY)
    service = MagicMock()
    service.calendars().get.return_value.execute.side_effect = _http_error(404)
    service.calendarList().list.return_value.execute.return_value = {
        "items": [{"id": "recovered-id", "summary": calendar_manager.APP_CALENDAR_SUMMARY, "accessRole": "owner"}]
    }

    result = calendar_manager.resolve_app_calendar(service)

    assert result == "recovered-id"
    service.calendars().insert.assert_not_called()
    assert calendar_manager.load_calendar_state()["calendar_id"] == "recovered-id"


def test_inaccessible_stored_calendar_with_no_recovery_match_creates_new(state_path):
    calendar_manager.save_calendar_state("stale-id", calendar_manager.APP_CALENDAR_SUMMARY)
    service = MagicMock()
    service.calendars().get.return_value.execute.side_effect = _http_error(403)
    service.calendarList().list.return_value.execute.return_value = {"items": []}
    service.calendars().insert.return_value.execute.return_value = {"id": "brand-new-id"}

    result = calendar_manager.resolve_app_calendar(service)

    assert result == "brand-new-id"


def test_recovery_search_ignores_calendars_not_owned_by_this_account(state_path):
    """A calendar merely shared with this account (accessRole != owner) must
    not be adopted as the dedicated app calendar."""
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {
        "items": [{"id": "shared-cal", "summary": calendar_manager.APP_CALENDAR_SUMMARY, "accessRole": "reader"}]
    }
    service.calendars().insert.return_value.execute.return_value = {"id": "created-id"}

    result = calendar_manager.resolve_app_calendar(service)

    assert result == "created-id"  # ignored the shared one, created its own


def test_create_if_missing_false_fails_closed_when_nothing_found(state_path):
    """clear_calendar.py's default path: no dedicated calendar configured ->
    fail safely, never fall back to any other calendar."""
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {"items": []}

    with pytest.raises(calendar_manager.CalendarStateError):
        calendar_manager.resolve_app_calendar(service, create_if_missing=False)

    service.calendars().insert.assert_not_called()


def test_create_if_missing_false_still_reuses_existing_calendar(state_path):
    calendar_manager.save_calendar_state("cal-123", calendar_manager.APP_CALENDAR_SUMMARY)
    service = MagicMock()
    service.calendars().get.return_value.execute.return_value = {"id": "cal-123"}

    result = calendar_manager.resolve_app_calendar(service, create_if_missing=False)

    assert result == "cal-123"


def test_resolve_never_returns_primary(state_path):
    service = MagicMock()
    service.calendarList().list.return_value.execute.return_value = {"items": []}
    service.calendars().insert.return_value.execute.return_value = {"id": "cal-xyz"}

    result = calendar_manager.resolve_app_calendar(service)

    assert result != "primary"


# ---------------------------------------------------------------------------
# OAuth credential resolution (no live Google account contact)
# ---------------------------------------------------------------------------

def test_build_oauth_service_fails_clearly_without_client_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_manager, "CALENDAR_OAUTH_TOKEN_PATH", tmp_path / "token.json")
    monkeypatch.setattr(calendar_manager, "CALENDAR_OAUTH_CLIENT_SECRETS_PATH", tmp_path / "missing_secrets.json")

    with pytest.raises(calendar_manager.CalendarAuthError):
        calendar_manager.build_oauth_calendar_service()


def test_build_oauth_service_reuses_valid_cached_token(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_OAUTH_TOKEN_PATH", token_path)

    fake_creds = MagicMock(valid=True)
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        lambda *a, **k: fake_creds,
    )
    token_path.write_text("{}", encoding="utf-8")

    built = {}

    def fake_build(*a, **k):
        built["creds"] = k.get("credentials")
        return "fake-service"

    monkeypatch.setattr("googleapiclient.discovery.build", fake_build)

    service = calendar_manager.build_oauth_calendar_service()

    assert service == "fake-service"
    assert built["creds"] is fake_creds
