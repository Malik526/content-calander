"""Tests for generate_calendar.py's calendar-targeting behavior: default
targets the dedicated app calendar via OAuth, --calendar is an explicit,
opt-in, service-account override, and dry-run never touches Google at all.
All Google API calls mocked."""

import sys
from unittest.mock import MagicMock

import pytest

import calendar_manager
import generate_calendar


def test_calendar_flag_defaults_to_none(monkeypatch):
    """No implicit target — not "primary", not DEFAULT_CALENDAR_ID."""
    monkeypatch.setattr(sys, "argv", ["generate_calendar.py"])
    args = generate_calendar.parse_args()
    assert args.calendar is None


def test_dry_run_never_touches_calendar_manager_or_service_account(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["generate_calendar.py", "--month", "6", "--year", "2026", "--dry-run"]
    )

    def _fail(*a, **k):
        raise AssertionError("dry-run must not call this")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _fail)
    monkeypatch.setattr(generate_calendar, "build_calendar_service", _fail)

    generate_calendar.main()  # must not raise

    out = capsys.readouterr().out
    assert "Dry run enabled" in out


def test_default_path_uses_oauth_and_dedicated_calendar(monkeypatch, tmp_path):
    """No --calendar: resolves the dedicated app calendar via OAuth, and
    events go to that calendar id — never the service account, never primary."""
    monkeypatch.setattr(sys, "argv", ["generate_calendar.py", "--month", "6", "--year", "2026"])

    fake_service = MagicMock()
    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", lambda: fake_service)
    monkeypatch.setattr(calendar_manager, "resolve_app_calendar", lambda service, **k: "dedicated-cal-id")

    def _service_account_should_not_be_called(*a, **k):
        raise AssertionError("default path must not use the service account")

    monkeypatch.setattr(generate_calendar, "build_calendar_service", _service_account_should_not_be_called)

    # Isolate persistence so this doesn't touch the real data/content.db.
    import content_store
    monkeypatch.setattr(content_store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(generate_calendar, "ContentStore", lambda: content_store.ContentStore(db_path=tmp_path / "test.db"))

    fake_service.events().insert.return_value.execute.return_value = {"id": "evt-1"}

    generate_calendar.main()  # must not raise

    calls = fake_service.events().insert.call_args_list
    assert len(calls) > 0
    assert all(call.kwargs["calendarId"] == "dedicated-cal-id" for call in calls)
    assert all(call.kwargs["calendarId"] != "primary" for call in calls)


def test_explicit_calendar_override_uses_service_account_not_oauth(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["generate_calendar.py", "--month", "6", "--year", "2026", "--calendar", "explicit-cal-id"],
    )

    fake_service = MagicMock()
    monkeypatch.setattr(generate_calendar, "build_calendar_service", lambda path: fake_service)

    def _oauth_should_not_be_called(*a, **k):
        raise AssertionError("explicit --calendar override must not use OAuth")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _oauth_should_not_be_called)
    monkeypatch.setattr(calendar_manager, "resolve_app_calendar", _oauth_should_not_be_called)

    import content_store
    monkeypatch.setattr(generate_calendar, "ContentStore", lambda: content_store.ContentStore(db_path=tmp_path / "test.db"))

    fake_service.events().insert.return_value.execute.return_value = {"id": "evt-1"}

    generate_calendar.main()

    calls = fake_service.events().insert.call_args_list
    assert len(calls) > 0
    assert all(call.kwargs["calendarId"] == "explicit-cal-id" for call in calls)
