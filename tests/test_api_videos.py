"""Tests for /api/videos (Milestone 3.7): authenticated batch upload,
ownership isolation, storage+DB persistence, partial-batch failure
behavior, and Library listing. Network/storage is real LocalStorage
against a tmp_path root (no Supabase call) — the FastAPI
routing/ownership/per-file-failure logic is exercised for real through
TestClient against a real temp-file SQLite ContentStore, matching
tests/test_api_platforms_tiktok.py's own established pattern.

The upload_batches/upload_attempts sections below (Milestone 3.7 follow-up
— upload performance instrumentation) inspect the store directly after
each request, the same way test_api_platforms_tiktok.py's own tenant-
isolation test does, since this telemetry is not itself exposed through
any API response."""

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


def test_uploading_the_same_bytes_twice_as_the_same_user_creates_two_distinct_videos(client, users):
    """Milestone 3.7 re-upload-architecture follow-up: a re-upload of
    byte-identical content is intentional (new caption/schedule/campaign),
    not a duplicate to collapse — it must succeed as its own new record."""
    user_a, _ = users
    _act_as(user_a)
    content = b"identical bytes"

    first = client.post("/api/videos", files=[("files", _mp4("v1.mp4", content))])
    second = client.post("/api/videos", files=[("files", _mp4("v1-retry.mp4", content))])

    assert first.json()["results"][0]["success"] is True
    assert second.json()["results"][0]["success"] is True
    assert second.json()["results"][0]["video"]["id"] != first.json()["results"][0]["video"]["id"]
    assert len(client.get("/api/videos").json()["videos"]) == 2  # two real, distinct records


def test_uploading_another_users_exact_content_also_succeeds_and_stays_tenant_isolated(client, users):
    """The cross-tenant mirror of the same-user case above — two different
    users uploading byte-identical content each get their own record and
    their own private Library; this is no longer rejected."""
    user_a, user_b = users
    content = b"shared bytes across two different accounts"

    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("a.mp4", content))])

    _act_as(user_b)
    response = client.post("/api/videos", files=[("files", _mp4("b.mp4", content))])

    result = response.json()["results"][0]
    assert result["success"] is True
    assert result["video"] is not None

    assert len(client.get("/api/videos").json()["videos"]) == 1  # user_b's own library, own record
    _act_as(user_a)
    assert len(client.get("/api/videos").json()["videos"]) == 1  # user_a's own is untouched


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


# ---------------------------------------------------------------------------
# upload performance instrumentation (Milestone 3.7 follow-up)
# ---------------------------------------------------------------------------

def test_upload_records_a_batch_and_one_attempt_per_file_with_timing(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)

    client.post(
        "/api/videos",
        files=[("files", _mp4("one.mp4", b"one-bytes")), ("files", _mp4("two.mp4", b"two-longer-bytes"))],
    )

    with ContentStore(db_path=db_path) as store:
        batches = store.list_upload_batches_for_user(user_a.id)
        assert len(batches) == 1
        batch = batches[0]
        assert batch.file_count == 2
        assert batch.status == "COMPLETED"
        assert batch.completed_at is not None
        assert batch.total_bytes == len(b"one-bytes") + len(b"two-longer-bytes")
        assert batch.total_duration_ms is not None and batch.total_duration_ms >= 0

        attempts = store.get_upload_attempts_for_batch(batch.id)
        assert len(attempts) == 2
        assert {a.original_filename for a in attempts} == {"one.mp4", "two.mp4"}
        for attempt in attempts:
            assert attempt.status == "SUCCESS"
            assert attempt.error_code is None
            assert attempt.completed_at is not None
            assert attempt.duration_ms is not None and attempt.duration_ms >= 0
            assert attempt.video_id is not None
            assert attempt.user_id == user_a.id
            assert attempt.batch_id == batch.id


def test_upload_records_failed_attempts_distinctly_from_successful_ones(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)

    client.post(
        "/api/videos",
        files=[("files", ("clip.txt", b"not a video", "text/plain")), ("files", _mp4("good.mp4", b"good"))],
    )

    with ContentStore(db_path=db_path) as store:
        batch = store.list_upload_batches_for_user(user_a.id)[0]
        attempts = {a.original_filename: a for a in store.get_upload_attempts_for_batch(batch.id)}

        assert attempts["clip.txt"].status == "FAILED"
        assert attempts["clip.txt"].error_code == "UNSUPPORTED_FILE_TYPE"
        assert attempts["clip.txt"].video_id is None

        assert attempts["good.mp4"].status == "SUCCESS"
        assert attempts["good.mp4"].error_code is None
        assert attempts["good.mp4"].video_id is not None

        # a batch with a mix of successful/failed attempts still completes —
        # there is no distinct batch-level "partial failure" status; the
        # real signal lives on each attempt (see module docstring).
        assert batch.status == "COMPLETED"


def test_upload_records_a_successful_attempt_for_re_uploaded_content_not_a_duplicate_error(client, users, db_path):
    """Re-upload architecture follow-up: there is no more DUPLICATE_CONTENT
    error_code — a re-upload of identical bytes is a normal successful
    attempt, telemetry included."""
    user_a, user_b = users
    content = b"shared bytes across two different accounts"

    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("a.mp4", content))])
    _act_as(user_b)
    client.post("/api/videos", files=[("files", _mp4("b.mp4", content))])

    with ContentStore(db_path=db_path) as store:
        batch_b = store.list_upload_batches_for_user(user_b.id)[0]
        attempt_b = store.get_upload_attempts_for_batch(batch_b.id)[0]
        assert attempt_b.status == "SUCCESS"
        assert attempt_b.error_code is None
        assert attempt_b.video_id is not None


def test_upload_telemetry_links_each_duplicate_hash_upload_to_its_own_distinct_video(client, users, db_path):
    """File-hash-architecture requirement: uploading the same bytes twice
    in one batch must not conflate the two attempts' video_id — each
    upload_attempts row must point at its own distinct videos row, not
    both at the same one (or either one at the wrong one)."""
    user_a, _ = users
    _act_as(user_a)
    content = b"duplicate bytes uploaded twice in one batch"

    response = client.post(
        "/api/videos",
        files=[("files", _mp4("first.mp4", content)), ("files", _mp4("second.mp4", content))],
    )
    results = {r["filename"]: r for r in response.json()["results"]}
    assert results["first.mp4"]["success"] is True
    assert results["second.mp4"]["success"] is True
    first_video_id = results["first.mp4"]["video"]["id"]
    second_video_id = results["second.mp4"]["video"]["id"]
    assert first_video_id != second_video_id

    with ContentStore(db_path=db_path) as store:
        batch = store.list_upload_batches_for_user(user_a.id)[0]
        attempts = {a.original_filename: a for a in store.get_upload_attempts_for_batch(batch.id)}
        assert attempts["first.mp4"].video_id == first_video_id
        assert attempts["second.mp4"].video_id == second_video_id
        # each attempt's video row genuinely holds this exact content
        assert store.get_video(attempts["first.mp4"].video_id).file_hash == \
            store.get_video(attempts["second.mp4"].video_id).file_hash


def test_upload_telemetry_is_isolated_per_tenant(client, users, db_path):
    """The batch/attempt tables carry user_id on every row and
    list_upload_batches_for_user is strictly scoped — the same tenant-
    isolation guarantee as videos themselves, proven directly here since
    telemetry has no API surface of its own to test through."""
    user_a, user_b = users
    _act_as(user_a)
    client.post("/api/videos", files=[("files", _mp4("a.mp4", b"a"))])
    _act_as(user_b)
    client.post("/api/videos", files=[("files", _mp4("b.mp4", b"b"))])

    with ContentStore(db_path=db_path) as store:
        batches_a = store.list_upload_batches_for_user(user_a.id)
        batches_b = store.list_upload_batches_for_user(user_b.id)

        assert len(batches_a) == 1 and len(batches_b) == 1
        assert batches_a[0].id != batches_b[0].id
        assert all(a.user_id == user_a.id for a in store.get_upload_attempts_for_batch(batches_a[0].id))
        assert all(a.user_id == user_b.id for a in store.get_upload_attempts_for_batch(batches_b[0].id))


# ---------------------------------------------------------------------------
# DELETE /api/videos/{video_id} (Milestone 3.7 follow-up — Delete Video)
# ---------------------------------------------------------------------------

def test_delete_requires_authentication(client, users):
    user_a, _ = users
    _act_as(user_a)
    video_id = client.post("/api/videos", files=[("files", _mp4("clip.mp4"))]).json()["results"][0]["video"]["id"]

    app_module.app.dependency_overrides.pop(auth_deps.get_current_user, None)
    response = client.delete(f"/api/videos/{video_id}")
    assert response.status_code == 401


def test_delete_removes_the_video_and_its_stored_object(client, users, tmp_path):
    user_a, _ = users
    _act_as(user_a)
    upload = client.post("/api/videos", files=[("files", _mp4("clip.mp4", b"delete me"))])
    video_id = upload.json()["results"][0]["video"]["id"]
    stored_files = list((tmp_path / "objects" / "users" / str(user_a.id)).rglob("source*"))
    assert len(stored_files) == 1

    response = client.delete(f"/api/videos/{video_id}")

    assert response.status_code == 204
    assert client.get("/api/videos").json()["videos"] == []
    assert list((tmp_path / "objects" / "users" / str(user_a.id)).rglob("source*")) == []


def test_delete_rejects_a_nonexistent_video(client, users):
    user_a, _ = users
    _act_as(user_a)
    response = client.delete("/api/videos/999999")
    assert response.status_code == 404


def test_delete_rejects_another_users_video_without_leaking_its_existence(client, users):
    user_a, user_b = users
    _act_as(user_a)
    video_id = client.post("/api/videos", files=[("files", _mp4("clip.mp4"))]).json()["results"][0]["video"]["id"]

    _act_as(user_b)
    response = client.delete(f"/api/videos/{video_id}")
    assert response.status_code == 404  # same status as "no such video" — see route docstring

    _act_as(user_a)
    assert len(client.get("/api/videos").json()["videos"]) == 1  # untouched


def test_delete_refuses_a_video_with_a_platform_post_and_leaves_it_intact(client, users, db_path):
    user_a, _ = users
    _act_as(user_a)
    video_id = client.post("/api/videos", files=[("files", _mp4("clip.mp4"))]).json()["results"][0]["video"]["id"]
    with ContentStore(db_path=db_path) as store:
        store.insert_platform_post(video_id, "tiktok", datetime.now(timezone.utc).isoformat(), user_id=user_a.id)

    response = client.delete(f"/api/videos/{video_id}")

    assert response.status_code == 409
    assert len(client.get("/api/videos").json()["videos"]) == 1  # not deleted


def test_deleting_a_video_lets_the_exact_same_file_be_uploaded_again(client, users):
    user_a, _ = users
    _act_as(user_a)
    content = b"identical bytes for re-upload"
    first = client.post("/api/videos", files=[("files", _mp4("clip.mp4", content))])
    video_id = first.json()["results"][0]["video"]["id"]

    assert client.delete(f"/api/videos/{video_id}").status_code == 204

    second = client.post("/api/videos", files=[("files", _mp4("clip-again.mp4", content))])
    result = second.json()["results"][0]
    assert result["success"] is True  # not rejected as a duplicate — the original row is gone
    assert result["video"]["id"] != video_id
    assert len(client.get("/api/videos").json()["videos"]) == 1
