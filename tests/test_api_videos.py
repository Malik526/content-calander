"""Tests for /api/videos (Milestone 3.7): authenticated batch upload,
ownership isolation, storage+DB persistence, partial-batch failure
behavior, and Library listing. Network/storage is real LocalStorage
against a tmp_path root (no Supabase call) — the FastAPI
routing/ownership/per-file-failure logic is exercised for real through
TestClient against a real temp-file SQLite ContentStore, matching
tests/test_api_platforms_tiktok.py's own established pattern."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from content_automation.api import app as app_module
from content_automation.api.dependencies import auth as auth_deps
from content_automation.api.dependencies import storage as storage_deps
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage


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


def _mp4(name: str, content: bytes = b"fake video bytes") -> tuple:
    return (name, content, "video/mp4")


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------

def test_upload_requires_authentication(client, users):
    app_module.app.dependency_overrides.pop(auth_deps.get_current_user, None)
    response = client.post("/api/videos", files=[("files", _mp4("a.mp4"))])
    assert response.status_code == 401


def test_upload_single_file_creates_owned_video_and_stores_it(client, users, tmp_path):
    user_a, _ = users
    _act_as(user_a)

    response = client.post("/api/videos", files=[("files", _mp4("clip.mp4", b"real bytes"))])

    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["success"] is True
    assert result["filename"] == "clip.mp4"
    assert result["video"]["original_filename"] == "clip.mp4"
    assert result["video"]["status"] == "DISCOVERED"
    assert result["video"]["file_size_bytes"] == len(b"real bytes")

    # stored for real, under this user's own tenant-scoped key
    stored_files = list((tmp_path / "objects" / "users" / str(user_a.id)).rglob("source*"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == b"real bytes"


def test_upload_batch_of_multiple_files_creates_one_video_each(client, users):
    user_a, _ = users
    _act_as(user_a)

    response = client.post(
        "/api/videos",
        files=[("files", _mp4("one.mp4", b"one")), ("files", _mp4("two.mp4", b"two"))],
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert {r["filename"] for r in results} == {"one.mp4", "two.mp4"}
    assert all(r["success"] for r in results)

    listing = client.get("/api/videos").json()["videos"]
    assert len(listing) == 2


def test_upload_rejects_unsupported_file_type_without_touching_the_rest_of_the_batch(client, users):
    """Phase 2 requirement 5: per-file failure, not a whole-batch failure —
    a bad file in the batch must not prevent the good ones from
    succeeding."""
    user_a, _ = users
    _act_as(user_a)

    response = client.post(
        "/api/videos",
        files=[("files", ("clip.txt", b"not a video", "text/plain")), ("files", _mp4("good.mp4", b"good"))],
    )

    assert response.status_code == 200
    results = {r["filename"]: r for r in response.json()["results"]}
    assert results["clip.txt"]["success"] is False
    assert "Unsupported file type" in results["clip.txt"]["error"]
    assert results["clip.txt"]["video"] is None
    assert results["good.mp4"]["success"] is True

    listing = client.get("/api/videos").json()["videos"]
    assert len(listing) == 1
    assert listing[0]["original_filename"] == "good.mp4"


def test_uploading_the_same_bytes_twice_as_the_same_user_is_idempotent_not_an_error(client, users):
    user_a, _ = users
    _act_as(user_a)
    content = b"identical bytes"

    first = client.post("/api/videos", files=[("files", _mp4("v1.mp4", content))])
    second = client.post("/api/videos", files=[("files", _mp4("v1-retry.mp4", content))])

    assert first.json()["results"][0]["success"] is True
    assert second.json()["results"][0]["success"] is True
    assert second.json()["results"][0]["video"]["id"] == first.json()["results"][0]["video"]["id"]
    assert len(client.get("/api/videos").json()["videos"]) == 1  # not duplicated


def test_uploading_another_users_exact_content_fails_cleanly_without_leaking_ownership(client, users):
    user_a, user_b = users
    content = b"shared bytes across two different accounts"

    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("a.mp4", content))])

    _act_as(user_b)
    response = client.post("/api/videos", files=[("files", _mp4("b.mp4", content))])

    result = response.json()["results"][0]
    assert result["success"] is False
    assert result["video"] is None
    assert result["error"] == "This exact video has already been uploaded."  # never names the other account/video

    assert client.get("/api/videos").json()["videos"] == []  # user_b's own library stays empty


# ---------------------------------------------------------------------------
# library listing — GET /api/videos
# ---------------------------------------------------------------------------

def test_list_videos_requires_authentication(client, users):
    app_module.app.dependency_overrides.pop(auth_deps.get_current_user, None)
    response = client.get("/api/videos")
    assert response.status_code == 401


def test_list_videos_returns_empty_list_when_none_uploaded(client, users):
    user_a, _ = users
    _act_as(user_a)
    response = client.get("/api/videos")
    assert response.status_code == 200
    assert response.json() == {"videos": []}


def test_list_videos_only_returns_the_current_users_own_videos(client, users):
    """Tenant isolation — the whole point of this endpoint existing at
    all, not an incidental property."""
    user_a, user_b = users
    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("a1.mp4", b"a1")), ("files", _mp4("a2.mp4", b"a2"))])

    _act_as(user_b)
    client.post("/api/videos", files=[("files", _mp4("b1.mp4", b"b1"))])

    _act_as(user_a)
    a_videos = client.get("/api/videos").json()["videos"]
    _act_as(user_b)
    b_videos = client.get("/api/videos").json()["videos"]

    assert {v["original_filename"] for v in a_videos} == {"a1.mp4", "a2.mp4"}
    assert {v["original_filename"] for v in b_videos} == {"b1.mp4"}


def test_list_videos_never_exposes_storage_key_or_local_paths(client, users):
    """Milestone 3.6's security-review lesson applied here too: no
    internal identifier (storage bucket key, server filesystem path) with
    no user value should ever reach the client — see
    api/schemas/videos.py's own docstring."""
    user_a, _ = users
    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("clip.mp4", b"bytes"))])

    body = client.get("/api/videos").json()["videos"][0]
    assert "storage_key" not in body
    assert "storage_provider" not in body
    assert "canonical_media_path" not in body
    assert "original_path" not in body
