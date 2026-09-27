"""Tests for /api/cadence (Milestone 3.8): hosted posting-cadence
configuration + future slot generation. Real temp-file SQLite ContentStore
via TestClient, matching tests/test_api_videos.py's own established
pattern — no mocking of the persistence layer, since this is exactly what
that layer is for."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.config import CADENCE_GENERATION_HORIZON_DAYS
from content_automation.persistence.content_store import ContentStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def users(db_path):
    with ContentStore(db_path=db_path) as store:
        user_a = store.create_user("a@example.com", "User A", "2026-01-01T00:00:00+00:00")
        user_b = store.create_user("b@example.com", "User B", "2026-01-01T00:00:00+00:00")
    return user_a, user_b


@pytest.fixture
def client(db_path):
    def _override_get_store():
        with ContentStore(db_path=db_path) as s:
            yield s

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _act_as(user):
    app_module.app.dependency_overrides[auth_deps.get_current_user] = lambda: user


def _weekday_two_mondays_out(tz: str) -> str:
    """Today's weekday name in `tz` — always within the generation horizon
    regardless of what day this test happens to run on."""
    from content_automation.calendar.cadence import WEEKDAY_NAMES

    return WEEKDAY_NAMES[datetime.now(ZoneInfo(tz)).weekday()]


def _a_different_weekday(weekday: str) -> str:
    """Any weekday name guaranteed distinct from `weekday` — used where a
    test needs two configs that provably don't overlap, without relying on
    two timezones' current weekday coincidentally differing."""
    from content_automation.calendar.cadence import WEEKDAY_NAMES

    idx = WEEKDAY_NAMES.index(weekday)
    return WEEKDAY_NAMES[(idx + 1) % 7]


def test_get_cadence_requires_authentication(client, users):
    response = client.get("/api/cadence")
    assert response.status_code == 401


def test_put_cadence_requires_authentication(client, users):
    response = client.put("/api/cadence", json={"timezone": "America/New_York", "posting_times": []})
    assert response.status_code == 401


def test_get_cadence_not_configured_returns_empty_shape(client, users):
    user_a, _ = users
    _act_as(user_a)

    response = client.get("/api/cadence")
    assert response.status_code == 200
    body = response.json()
    assert body == {"configured": False, "timezone": None, "is_active": False, "posting_times": []}


def test_put_cadence_saves_and_generates_slots(client, users):
    user_a, _ = users
    _act_as(user_a)
    today_name = _weekday_two_mondays_out("America/New_York")

    response = client.put(
        "/api/cadence",
        json={
            "timezone": "America/New_York",
            "is_active": True,
            "posting_times": [{"weekday": today_name, "posting_time": "09:00"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["timezone"] == "America/New_York"
    assert body["is_active"] is True
    assert body["posting_times"] == [{"weekday": today_name, "posting_time": "09:00"}]

    slots_response = client.get("/api/cadence/slots")
    assert slots_response.status_code == 200
    slots = slots_response.json()["slots"]
    assert len(slots) >= 1
    assert all(s["status"] == "OPEN" and s["timezone"] == "America/New_York" for s in slots)


def test_put_cadence_rejects_unknown_timezone(client, users):
    user_a, _ = users
    _act_as(user_a)
    response = client.put(
        "/api/cadence", json={"timezone": "Not/A_Zone", "posting_times": [{"weekday": "monday", "posting_time": "09:00"}]},
    )
    assert response.status_code == 400


def test_put_cadence_rejects_unknown_weekday(client, users):
    user_a, _ = users
    _act_as(user_a)
    response = client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "posting_times": [{"weekday": "funday", "posting_time": "09:00"}]},
    )
    assert response.status_code == 400


def test_changing_cadence_reconciles_old_future_open_slots(client, users, db_path):
    """The regression case from plan review: editing a cadence must not
    leave the previous config's future OPEN slots behind."""
    user_a, _ = users
    _act_as(user_a)
    day1 = _weekday_two_mondays_out("America/New_York")

    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": day1, "posting_time": "09:00"}]},
    )
    first_slots = {s["scheduled_at"] for s in client.get("/api/cadence/slots").json()["slots"]}
    assert first_slots

    other_day = _a_different_weekday(day1)
    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": other_day, "posting_time": "18:00"}]},
    )
    second_slots = {s["scheduled_at"] for s in client.get("/api/cadence/slots").json()["slots"]}

    assert first_slots.isdisjoint(second_slots)  # the old 09:00 slots are gone, not just added-to
    with ContentStore(db_path=db_path) as store:
        rows = store._conn.execute(
            "SELECT scheduled_at FROM content_slots WHERE user_id = ? AND status = 'OPEN'", (user_a.id,)
        ).fetchall()
        assert all("09:00:00" not in r["scheduled_at"] for r in rows)
        assert any("18:00:00" in r["scheduled_at"] for r in rows)


def test_setting_inactive_clears_future_open_slots(client, users):
    user_a, _ = users
    _act_as(user_a)
    day = _weekday_two_mondays_out("America/New_York")

    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": day, "posting_time": "09:00"}]},
    )
    assert client.get("/api/cadence/slots").json()["slots"]

    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": False, "posting_times": [{"weekday": day, "posting_time": "09:00"}]},
    )
    assert client.get("/api/cadence/slots").json()["slots"] == []


def test_assigned_slot_survives_cadence_changes(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)
    day = _weekday_two_mondays_out("America/New_York")

    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": day, "posting_time": "09:00"}]},
    )
    with ContentStore(db_path=db_path) as store:
        video = store.insert_video("h1", "f.mp4", "hosted-upload/h1", "2026-01-01T00:00:00", user_id=user_a.id)
        slot = store.list_content_slots_for_user(
            user_a.id, "2020-01-01T00:00:00", "2100-01-01T00:00:00"
        )[0]
        store.assign_slot(video.id, slot.id)

    # change the cadence entirely, including deactivating it
    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": False, "posting_times": []},
    )

    with ContentStore(db_path=db_path) as store:
        refreshed = store.get_slot(slot.id)
        assert refreshed.status == "ASSIGNED"
        assert refreshed.assigned_video_id == video.id


def test_different_users_cadences_are_isolated(client, users):
    user_a, user_b = users
    day = _weekday_two_mondays_out("America/New_York")

    _act_as(user_a)
    client.put(
        "/api/cadence",
        json={"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": day, "posting_time": "09:00"}]},
    )

    _act_as(user_b)
    b_cadence = client.get("/api/cadence").json()
    assert b_cadence["configured"] is False
    assert client.get("/api/cadence/slots").json()["slots"] == []


def test_two_users_same_weekday_and_time_both_get_slots(client, users, db_path):
    """Direct end-to-end proof of the per-user uniqueness fix: two users
    configuring the identical cadence must both get their own slots, not
    silently collide via the old global UNIQUE(scheduled_at)."""
    user_a, user_b = users
    day = _weekday_two_mondays_out("America/New_York")
    payload = {"timezone": "America/New_York", "is_active": True, "posting_times": [{"weekday": day, "posting_time": "09:00"}]}

    _act_as(user_a)
    client.put("/api/cadence", json=payload)
    _act_as(user_b)
    client.put("/api/cadence", json=payload)

    with ContentStore(db_path=db_path) as store:
        a_slots = store.list_content_slots_for_user(user_a.id, "2020-01-01T00:00:00", "2100-01-01T00:00:00")
        b_slots = store.list_content_slots_for_user(user_b.id, "2020-01-01T00:00:00", "2100-01-01T00:00:00")
        assert len(a_slots) >= 1
        assert len(b_slots) >= 1
        assert {s.scheduled_at for s in a_slots} == {s.scheduled_at for s in b_slots}  # same wall-clock strings
