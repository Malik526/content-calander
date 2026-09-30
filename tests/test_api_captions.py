"""Tests for /api/videos/{id}/caption (Milestone 3.10: Caption Generation +
Editing) and the caption carried on /api/queue slots. Real temp-file SQLite
ContentStore via TestClient, same fixture pattern as tests/test_api_queue.py."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.api.dependencies import storage as storage_deps
from content_automation.config import CAPTION_TEXT_MAX_CHARS
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage

NOW = datetime(2026, 9, 30, 8, 0, 0)


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

    def _override_get_storage():
        return LocalStorage(root=tmp_path / "objects")

    app_module.app.dependency_overrides[auth_deps.get_store] = _override_get_store
    app_module.app.dependency_overrides[storage_deps.get_storage] = _override_get_storage
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()


def _act_as(user):
    app_module.app.dependency_overrides[auth_deps.get_current_user] = lambda: user


def _video(db_path, user, name="v", file_hash=None, **fields):
    with ContentStore(db_path=db_path) as store:
        video = store.insert_video(
            file_hash or f"hash-{name}", f"{name}.mp4", f"/incoming/{name}.mp4", NOW.isoformat(), user_id=user.id,
        )
        if fields:
            store.update_video(video.id, **fields)
        return video.id


def _stored(db_path, video_id):
    with ContentStore(db_path=db_path) as store:
        return store.get_video(video_id)


# --- auth / ownership ----------------------------------------------------------

def test_caption_routes_require_authentication(client, users, db_path):
    video_id = _video(db_path, users[0])
    assert client.get(f"/api/videos/{video_id}/caption").status_code == 401
    assert client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "x"}).status_code == 401
    assert client.post(f"/api/videos/{video_id}/caption/generate", json={}).status_code == 401


def test_cannot_read_or_write_another_users_caption(client, users, db_path):
    user_a, user_b = users
    video_id = _video(db_path, user_a, transcript="words", caption_text="A's caption", caption_source="manual")
    _act_as(user_b)

    assert client.get(f"/api/videos/{video_id}/caption").status_code == 404
    assert client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "hijack"}).status_code == 404
    assert client.post(f"/api/videos/{video_id}/caption/generate", json={"overwrite": True}).status_code == 404
    # Indistinguishable from a video that doesn't exist at all.
    assert client.get("/api/videos/99999/caption").json() == client.get(f"/api/videos/{video_id}/caption").json()

    stored = _stored(db_path, video_id)
    assert stored.caption_text == "A's caption"
    assert stored.caption_source == "manual"


# --- read / write ----------------------------------------------------------------

def test_get_caption_for_new_upload(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0])
    body = client.get(f"/api/videos/{video_id}/caption").json()
    assert body == {"video_id": video_id, "caption_text": None, "provenance": "NONE", "can_generate": False, "editable": True}


def test_manual_caption_persists_across_requests(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0])

    saved = client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "my caption #fyp"})
    assert saved.status_code == 200
    assert saved.json()["provenance"] == "MANUAL"

    # A fresh request (a page refresh) sees the persisted value.
    refreshed = client.get(f"/api/videos/{video_id}/caption").json()
    assert refreshed["caption_text"] == "my caption #fyp"
    assert refreshed["provenance"] == "MANUAL"
    assert _stored(db_path, video_id).caption_source == "manual"


def test_caption_response_never_exposes_transcript_or_raw_source(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0], transcript="secret spoken words")
    body = client.get(f"/api/videos/{video_id}/caption").json()
    assert "transcript" not in body
    assert "caption_source" not in body
    assert "secret spoken words" not in str(body)


def test_caption_over_max_length_is_rejected(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0])
    response = client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "x" * (CAPTION_TEXT_MAX_CHARS + 1)})
    assert response.status_code == 422
    assert _stored(db_path, video_id).caption_text is None


def test_identical_hash_videos_have_independent_captions(client, users, db_path):
    _act_as(users[0])
    video_a = _video(db_path, users[0], name="a", file_hash="xyz")
    video_b = _video(db_path, users[0], name="b", file_hash="xyz")

    client.put(f"/api/videos/{video_a}/caption", json={"caption_text": "caption A"})
    client.put(f"/api/videos/{video_b}/caption", json={"caption_text": "completely different"})

    assert client.get(f"/api/videos/{video_a}/caption").json()["caption_text"] == "caption A"
    assert client.get(f"/api/videos/{video_b}/caption").json()["caption_text"] == "completely different"


# --- generation ------------------------------------------------------------------

def test_generate_without_transcript_is_409_and_writes_nothing(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0])
    response = client.post(f"/api/videos/{video_id}/caption/generate", json={})
    assert response.status_code == 409
    assert "transcript" in response.json()["detail"].lower()
    assert _stored(db_path, video_id).caption_source is None


def test_generated_caption_can_then_be_edited(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0], transcript="hello   world")

    generated = client.post(f"/api/videos/{video_id}/caption/generate", json={}).json()
    assert generated["caption_text"] == "hello world"
    assert generated["provenance"] == "GENERATED"
    assert generated["can_generate"] is True

    edited = client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "hello world, edited"}).json()
    assert edited["caption_text"] == "hello world, edited"
    assert edited["provenance"] == "GENERATED_EDITED"
    assert _stored(db_path, video_id).caption_source == "transcript_auto_edited"


def test_regeneration_requires_explicit_overwrite(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0], transcript="spoken words", caption_text="hand edited", caption_source="manual")

    refused = client.post(f"/api/videos/{video_id}/caption/generate", json={})
    assert refused.status_code == 409
    assert _stored(db_path, video_id).caption_text == "hand edited"

    replaced = client.post(f"/api/videos/{video_id}/caption/generate", json={"overwrite": True})
    assert replaced.status_code == 200
    assert replaced.json()["caption_text"] == "spoken words"
    assert replaced.json()["provenance"] == "GENERATED"


def test_caption_is_locked_after_submission(client, users, db_path):
    _act_as(users[0])
    video_id = _video(db_path, users[0], caption_text="sent", caption_source="manual")
    with ContentStore(db_path=db_path) as store:
        post = store.insert_platform_post(video_id, "tiktok", created_at=NOW.isoformat(), user_id=users[0].id)
        store.update_platform_post(post.id, updated_at=NOW.isoformat(), status="PUBLISHED", platform_post_id="pub_1")

    assert client.get(f"/api/videos/{video_id}/caption").json()["editable"] is False
    assert client.put(f"/api/videos/{video_id}/caption", json={"caption_text": "changed"}).status_code == 409
    assert _stored(db_path, video_id).caption_text == "sent"


# --- Queue exposure ----------------------------------------------------------------

def test_assigned_queue_item_exposes_its_caption(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)
    video_id = _video(db_path, user_a, caption_text="queued caption", caption_source="manual")
    with ContentStore(db_path=db_path) as store:
        store.insert_slot_if_missing("2026-10-05T09:00:00", None, None, NOW.isoformat(), user_id=user_a.id)
        slot_id = store.list_content_slots_for_user(user_a.id, "2000-01-01T00:00:00", "2200-01-01T00:00:00")[0].id

    assigned = client.post(f"/api/queue/slots/{slot_id}/assign", json={"video_id": video_id})
    assert assigned.status_code == 200

    slots = client.get("/api/queue/slots", params={"from": "2000-01-01T00:00:00", "to": "2200-01-01T00:00:00"}).json()["slots"]
    caption = slots[0]["assigned_video"]["caption"]
    assert caption == {
        "video_id": video_id, "caption_text": "queued caption", "provenance": "MANUAL",
        "can_generate": False, "editable": True,
    }
