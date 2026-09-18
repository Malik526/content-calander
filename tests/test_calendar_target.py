"""Tests for generate_calendar.py's calendar-targeting behavior: default
targets the dedicated app calendar via OAuth, --calendar is an explicit,
opt-in, service-account override, and dry-run never touches Google at all.
All Google API calls mocked."""

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

# Milestone 3.0 package refactor: build_schedule()'s own free variables
# (e.g. now_in_config_timezone) resolve against the PACKAGE module's
# globals, so patching those must target `generate_calendar` (the
# package). main()/parse_args() and what main() itself calls
# (build_calendar_service, ContentStore) live in the thin CLI wrapper
# (cli/generate_calendar.py, importable bare via pytest.ini's
# pythonpath), so those are patched on `generate_calendar_cli` instead —
# patching the package copy would not affect what main() actually calls.
import generate_calendar as generate_calendar_cli
from content_automation.calendar import calendar_manager, generate_calendar


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    """These tests target month=6/year=2026 as "the full month" — fix "now"
    to well before that so future-only filtering (see
    tests/test_future_only_schedule.py) never empties the schedule here."""
    monkeypatch.setattr(generate_calendar, "now_in_config_timezone", lambda: datetime(2020, 1, 1))


def test_calendar_flag_defaults_to_none(monkeypatch):
    """No implicit target — not "primary", not DEFAULT_CALENDAR_ID."""
    monkeypatch.setattr(sys, "argv", ["generate_calendar.py"])
    args = generate_calendar_cli.parse_args()
    assert args.calendar is None


def test_dry_run_never_touches_calendar_manager_or_service_account(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["generate_calendar.py", "--month", "6", "--year", "2026", "--dry-run"]
    )

    def _fail(*a, **k):
        raise AssertionError("dry-run must not call this")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _fail)
    monkeypatch.setattr(generate_calendar_cli, "build_calendar_service", _fail)

    generate_calendar_cli.main()  # must not raise

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

    monkeypatch.setattr(generate_calendar_cli, "build_calendar_service", _service_account_should_not_be_called)

    # Isolate persistence so this doesn't touch the real data/content.db.
    from content_automation.persistence import content_store
    monkeypatch.setattr(content_store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(generate_calendar_cli, "ContentStore", lambda: content_store.ContentStore(db_path=tmp_path / "test.db"))

    fake_service.events().insert.return_value.execute.return_value = {"id": "evt-1"}

    generate_calendar_cli.main()  # must not raise

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
    monkeypatch.setattr(generate_calendar_cli, "build_calendar_service", lambda path: fake_service)

    def _oauth_should_not_be_called(*a, **k):
        raise AssertionError("explicit --calendar override must not use OAuth")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _oauth_should_not_be_called)
    monkeypatch.setattr(calendar_manager, "resolve_app_calendar", _oauth_should_not_be_called)

    from content_automation.persistence import content_store
    monkeypatch.setattr(generate_calendar_cli, "ContentStore", lambda: content_store.ContentStore(db_path=tmp_path / "test.db"))

    fake_service.events().insert.return_value.execute.return_value = {"id": "evt-1"}

    generate_calendar_cli.main()

    calls = fake_service.events().insert.call_args_list
    assert len(calls) > 0
    assert all(call.kwargs["calendarId"] == "explicit-cal-id" for call in calls)
