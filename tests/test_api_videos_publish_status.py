"""Tests for VideoResponse.publish_status on GET /api/videos (Milestone 3.14
final follow-up: Library publishing status). The Library must show the same
publishing truth as the Queue, so every case asserts the video's
publish_status equals its slot's display_status from GET /api/queue/slots.
Same TestClient + temp SQLite fixture pattern as test_api_queue_publish_status.py."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.api.dependencies import storage as storage_deps
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage

NOW = datetime(2026, 9, 30, 8, 0, 0)
WINDOW = {"from": "2000-01-01T00:00:00", "to": "2200-01-01T00:00:00"}
FUTURE = "2099-01-05T09:00:00"


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
def client(db_path, tmp_path):
    def _override_get_store():
        with ContentStore(db_path=db_path) as s:
            yield s

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    app_module.app.dependency_overrides[storage_deps.get_storage] = lambda: LocalStorage(root=tmp_path / "objects")
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _act_as(user):
    app_module.app.dependency_overrides[auth_deps.get_current_user] = lambda: user


def _unscheduled_video(db_path, user, name="loose"):
    with ContentStore(db_path=db_path) as store:
        return store.insert_video(f"hash-{name}", f"{name}.mp4", f"/in/{name}.mp4", NOW.isoformat(), user_id=user.id).id


def _scheduled(client, db_path, user, scheduled_at=FUTURE, name="v", **post_fields):
    """An assigned slot (via the real assign endpoint) whose PENDING post is
    then moved to whatever state the test needs. Returns the video id."""
    with ContentStore(db_path=db_path) as store:
        store.insert_slot_if_missing(scheduled_at, None, None, NOW.isoformat(), user_id=user.id)
        slot_id = store._conn.execute(
            "SELECT id FROM content_slots WHERE user_id = ? AND scheduled_at = ?", (user.id, scheduled_at)
        ).fetchone()["id"]
    video_id = _unscheduled_video(db_path, user, name)
    _act_as(user)
    assert client.post(f"/api/queue/slots/{slot_id}/assign", json={"video_id": video_id}).status_code == 200
    if post_fields:
        with ContentStore(db_path=db_path) as store:
            post = store.get_platform_post(video_id, "tiktok")
            store.update_platform_post(post.id, updated_at=post_fields.pop("updated_at", NOW.isoformat()), **post_fields)
    return video_id


def _library_status(client, video_id):
    videos = client.get("/api/videos").json()["videos"]
    return next(v["publish_status"] for v in videos if v["id"] == video_id)


def _queue_status(client, video_id):
    slots = client.get("/api/queue/slots", params=WINDOW).json()["slots"]
    return next(s["display_status"] for s in slots if s["assigned_video"] and s["assigned_video"]["id"] == video_id)


def test_video_without_a_slot_is_unscheduled(client, users, db_path):
    video_id = _unscheduled_video(db_path, users[0])
    _act_as(users[0])
    assert _library_status(client, video_id) == "UNSCHEDULED"


def test_pending_scheduled_post_is_scheduled(client, users, db_path):
    video_id = _scheduled(client, db_path, users[0])
    assert _library_status(client, video_id) == "SCHEDULED" == _queue_status(client, video_id)


def test_in_progress_post_is_publishing(client, users, db_path):
    video_id = _scheduled(
        client, db_path, users[0], status="PUBLISHING", updated_at=datetime.now(timezone.utc).isoformat(),
    )
    assert _library_status(client, video_id) == "PUBLISHING" == _queue_status(client, video_id)


def test_confirmed_published_post_is_published_not_scheduled(client, users, db_path):
    video_id = _scheduled(
        client, db_path, users[0], status="PUBLISHED", platform_post_id="pub_1",
        published_at="2026-09-29T13:00:05+00:00",
    )
    assert _library_status(client, video_id) == "PUBLISHED" == _queue_status(client, video_id)


def test_failed_post_is_failed(client, users, db_path):
    video_id = _scheduled(client, db_path, users[0], status="FAILED", failure_code="CAPTION_TOO_LONG")
    assert _library_status(client, video_id) == "FAILED" == _queue_status(client, video_id)


def test_unknown_outcome_needs_attention(client, users, db_path):
    video_id = _scheduled(client, db_path, users[0], status="UNKNOWN")
    assert _library_status(client, video_id) == "NEEDS_ATTENTION" == _queue_status(client, video_id)


def test_published_without_a_platform_id_is_never_shown_as_published(client, users, db_path):
    # The Queue's own STATE_INCONSISTENT rule applies to the Library too.
    video_id = _scheduled(client, db_path, users[0], status="PUBLISHED", published_at="2026-09-29T13:00:05+00:00")
    assert _library_status(client, video_id) == "NEEDS_ATTENTION" == _queue_status(client, video_id)


def test_freshly_uploaded_video_is_unscheduled(client, users):
    _act_as(users[0])
    response = client.post("/api/videos", files=[("files", ("new.mp4", b"bytes", "video/mp4"))])
    assert response.json()["results"][0]["video"]["publish_status"] == "UNSCHEDULED"


def test_publish_status_stays_tenant_isolated(client, users, db_path):
    user_a, user_b = users
    _scheduled(client, db_path, user_a, status="PUBLISHED", platform_post_id="pub_1")
    _act_as(user_b)
    assert client.get("/api/videos").json()["videos"] == []
