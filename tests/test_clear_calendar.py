"""Tests for clear_calendar.py's dedicated-app-calendar clear path: scoped
to content_slots, never the primary/an arbitrary calendar, dry-run is
non-mutating, and OPEN vs --all scoping. All Google API calls mocked."""

from unittest.mock import MagicMock

import pytest

import calendar_manager
import clear_calendar
from content_store import ContentStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(clear_calendar, "ContentStore", lambda: ContentStore(db_path=db_path))
    with ContentStore(db_path=db_path) as s:
        yield s


def _seed_open_slot(store, scheduled_at="2026-09-14T09:00:00", event_id="evt-open"):
    store.insert_slot_if_missing(scheduled_at, "engineering", "prompt", "2026-01-01T00:00:00", google_calendar_event_id=event_id)


def _seed_assigned_slot(store, scheduled_at="2026-09-15T09:00:00", event_id="evt-assigned"):
    store.insert_slot_if_missing(scheduled_at, "career", "prompt", "2026-01-01T00:00:00", google_calendar_event_id=event_id)
    slot = store.find_earliest_open_slot("career", "2000-01-01T00:00:00")
    video = store.insert_video("hash1", "video.mp4", "/incoming/video.mp4", "2026-01-01T00:00:00")
    store.assign_slot(video.id, slot.id)
    return slot.id, video.id


@pytest.fixture(autouse=True)
def oauth_and_calendar(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_manager, "APP_CALENDAR_STATE_PATH", tmp_path / "calendar_state.json")
    fake_service = MagicMock()
    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", lambda: fake_service)
    return fake_service


def test_dry_run_reports_without_deleting(store, oauth_and_calendar, capsys):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    _seed_open_slot(store)

    clear_calendar._clear_dedicated_calendar(dry_run=True, include_assigned=False)

    oauth_and_calendar.events().delete.assert_not_called()
    remaining = store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00")
    assert remaining is not None  # slot still there — nothing was mutated
    assert "Dry run" in capsys.readouterr().out


def test_default_clear_removes_open_slots_and_their_events(store, oauth_and_calendar):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    _seed_open_slot(store, event_id="evt-open")

    clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=False)

    oauth_and_calendar.events().delete.assert_called_once_with(calendarId="cal-1", eventId="evt-open")
    assert store.find_earliest_open_slot("engineering", "2000-01-01T00:00:00") is None


def test_default_clear_never_touches_assigned_slots(store, oauth_and_calendar):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    slot_id, video_id = _seed_assigned_slot(store)

    clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=False)

    oauth_and_calendar.events().delete.assert_not_called()
    row = store._conn.execute("SELECT status FROM content_slots WHERE id = ?", (slot_id,)).fetchone()
    assert row["status"] == "ASSIGNED"  # untouched


def test_all_flag_also_clears_assigned_and_resets_video(store, oauth_and_calendar):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    slot_id, video_id = _seed_assigned_slot(store, event_id="evt-assigned")

    clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=True)

    oauth_and_calendar.events().delete.assert_called_once_with(calendarId="cal-1", eventId="evt-assigned")
    row = store._conn.execute("SELECT * FROM content_slots WHERE id = ?", (slot_id,)).fetchone()
    assert row is None  # slot deleted
    video = store.get_video_by_hash("hash1")
    assert video.assigned_slot_id is None
    assert video.status == "CLASSIFIED"  # reset, not destroyed


def test_calendar_itself_is_never_deleted(store, oauth_and_calendar):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    _seed_open_slot(store)

    clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=False)

    oauth_and_calendar.calendars().delete.assert_not_called()


def test_dry_run_prints_fifo_label_for_null_pillar_slot(store, oauth_and_calendar, capsys):
    """A FIFO slot (pillar_key=None) must not print the literal '[None]'."""
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    store.insert_slot_if_missing("2026-09-14T09:00:00", None, None, "2026-01-01T00:00:00", google_calendar_event_id="evt-fifo")

    clear_calendar._clear_dedicated_calendar(dry_run=True, include_assigned=False)

    out = capsys.readouterr().out
    assert "[fifo]" in out
    assert "[None]" not in out


def test_clear_prints_fifo_label_for_null_pillar_slot(store, oauth_and_calendar, capsys):
    calendar_manager.save_calendar_state("cal-1", calendar_manager.APP_CALENDAR_SUMMARY)
    oauth_and_calendar.calendars().get.return_value.execute.return_value = {"id": "cal-1"}
    store.insert_slot_if_missing("2026-09-14T09:00:00", None, None, "2026-01-01T00:00:00", google_calendar_event_id="evt-fifo")

    clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=False)

    out = capsys.readouterr().out
    assert "[fifo]" in out
    assert "[None]" not in out


def test_no_dedicated_calendar_fails_closed_not_primary(store, oauth_and_calendar):
    """No persisted/discoverable app calendar -> fail safely; must never
    fall back to clearing 'primary' or any other calendar."""
    oauth_and_calendar.calendarList().list.return_value.execute.return_value = {"items": []}

    with pytest.raises(SystemExit):
        clear_calendar._clear_dedicated_calendar(dry_run=False, include_assigned=False)

    oauth_and_calendar.calendars().insert.assert_not_called()
    oauth_and_calendar.events().delete.assert_not_called()


# ---------------------------------------------------------------------------
# Explicit --calendar override (advanced/debug, service account, opt-in)
# ---------------------------------------------------------------------------

def test_explicit_calendar_override_uses_service_account_and_given_id(monkeypatch):
    fake_service = MagicMock()
    fake_service.events().list.return_value.execute.return_value = {
        "items": [{"id": "evt-1", "summary": "POST", "start": {"date": "2026-06-01"}}]
    }
    monkeypatch.setattr(clear_calendar, "build_calendar_service", lambda path: fake_service)

    def _oauth_should_not_be_called(*a, **k):
        raise AssertionError("explicit --calendar override must not use OAuth")

    monkeypatch.setattr(calendar_manager, "build_oauth_calendar_service", _oauth_should_not_be_called)

    clear_calendar._clear_explicit_calendar("explicit-cal-id", None, None, dry_run=False)

    fake_service.events().delete.assert_called_once_with(calendarId="explicit-cal-id", eventId="evt-1")


def test_explicit_calendar_override_dry_run_does_not_delete(monkeypatch):
    fake_service = MagicMock()
    fake_service.events().list.return_value.execute.return_value = {
        "items": [{"id": "evt-1", "summary": "POST", "start": {"date": "2026-06-01"}}]
    }
    monkeypatch.setattr(clear_calendar, "build_calendar_service", lambda path: fake_service)

    clear_calendar._clear_explicit_calendar("explicit-cal-id", "2026-06-01", "2026-07-01", dry_run=True)

    fake_service.events().delete.assert_not_called()
