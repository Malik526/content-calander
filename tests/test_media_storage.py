"""Tests for media.media_storage — the domain-level bridge between a
video's DB row and its object-storage backend (Milestone 3.4). Uses a real
(temp-file) ContentStore + LocalStorage — no network dependency; the
ownership/idempotency/materialization contract this module establishes is
backend-agnostic, exercised here against the fast local backend and again
against real Supabase in tests/test_storage_supabase.py."""

from datetime import datetime

import pytest

from content_automation.media import media_storage
from content_automation.persistence.content_store import ContentStore
from content_automation.storage.local import LocalStorage

NOW = datetime(2026, 9, 14, 8, 0, 0)


@pytest.fixture
def store(tmp_path):
    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


def _video_with_local_media(store, tmp_path, user_id, *, name="clip"):
    video_path = tmp_path / f"{name}.mp4"
    video_path.write_bytes(f"{name} bytes".encode() * 100)
    video = store.insert_video(f"hash-{name}", f"{name}.mp4", str(video_path), NOW.isoformat(), user_id=user_id)
    store.update_video(video.id, canonical_media_path=str(video_path))
    return store.get_video(video.id), video_path


def test_build_storage_key_is_tenant_scoped_and_deterministic():
    key1 = media_storage.build_storage_key(user_id=3, video_id=12, suffix=".mp4")
    key2 = media_storage.build_storage_key(user_id=3, video_id=12, suffix=".mp4")
    key_other_user = media_storage.build_storage_key(user_id=4, video_id=12, suffix=".mp4")

    assert key1 == key2  # deterministic
    assert key1 == "users/3/videos/12/source.mp4"
    assert key1 != key_other_user
    assert "3" in key1 and "12" in key1


def test_upload_canonical_media_stamps_storage_fields(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    updated = media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert updated.storage_provider == "local"
    assert updated.storage_key == f"users/{user.id}/videos/{video.id}/source.mp4"
    assert storage.exists(updated.storage_key)


def test_upload_canonical_media_never_deletes_local_source(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert video_path.exists()  # original local file untouched — see ADR "Retention"


def test_upload_canonical_media_is_idempotent(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    first = media_storage.upload_canonical_media(store, storage, video.id, user.id)
    second = media_storage.upload_canonical_media(store, storage, video.id, user.id)

    assert first.storage_key == second.storage_key  # same deterministic key, overwritten not duplicated


def test_upload_canonical_media_rejects_wrong_user(store, storage, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video, _ = _video_with_local_media(store, tmp_path, user_a.id)

    with pytest.raises(media_storage.MediaOwnershipError):
        media_storage.upload_canonical_media(store, storage, video.id, user_b.id)


def test_upload_canonical_media_requires_a_local_file_first(store, storage):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = store.insert_video("hash-empty", "clip.mp4", "/incoming/clip.mp4", NOW.isoformat(), user_id=user.id)

    with pytest.raises(media_storage.MediaNotUploadedError):
        media_storage.upload_canonical_media(store, storage, video.id, user.id)


def test_materialize_canonical_media_uses_storage_when_uploaded(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)
    media_storage.upload_canonical_media(store, storage, video.id, user.id)
    video_path.unlink()  # prove materialize reads from storage, not the (now-deleted) local original

    with media_storage.materialize_canonical_media(store, storage, video.id, user.id) as materialized:
        assert materialized.read_bytes() == b"clip bytes" * 100


def test_materialize_canonical_media_falls_back_to_local_path_when_not_uploaded(store, storage, tmp_path):
    """A video never uploaded to object storage (storage_provider NULL —
    every pre-3.4 video) must materialize directly from
    canonical_media_path, with zero calls to `storage` — proven here by
    passing a storage instance whose root doesn't even exist yet."""
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    with media_storage.materialize_canonical_media(store, storage, video.id, user.id) as materialized:
        assert materialized == video_path
        assert materialized.read_bytes() == video_path.read_bytes()


def test_materialize_canonical_media_rejects_wrong_user(store, storage, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video, _ = _video_with_local_media(store, tmp_path, user_a.id)
    media_storage.upload_canonical_media(store, storage, video.id, user_a.id)

    with pytest.raises(media_storage.MediaOwnershipError):
        with media_storage.materialize_canonical_media(store, storage, video.id, user_b.id):
            pass


# ---------------------------------------------------------------------------
# create_video_from_upload (Milestone 3.7 — hosted batch upload)
# ---------------------------------------------------------------------------

def _upload_tmp_file(tmp_path, *, name="upload", content=b"upload bytes") -> "Path":
    from pathlib import Path

    path = Path(tmp_path) / f"{name}.mp4"
    path.write_bytes(content)
    return path


def test_create_video_from_upload_creates_an_owned_row_and_stores_bytes(store, storage, tmp_path):
    from content_automation.media.inspection import file_hash

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path)

    video = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=file_hash(upload_path), file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert video.user_id == user.id
    assert video.original_filename == "clip.mp4"
    assert video.status == "DISCOVERED"
    assert video.canonical_media_path is None  # never the temp path — see function docstring
    assert video.storage_provider == "local"
    assert video.storage_key == f"users/{user.id}/videos/{video.id}/source.mp4"
    assert video.file_size_bytes == upload_path.stat().st_size
    assert storage.exists(video.storage_key)


def test_create_video_from_upload_creates_a_distinct_record_for_the_same_user_re_uploading_identical_content(
    store, storage, tmp_path,
):
    """Milestone 3.7 re-upload-architecture follow-up: a creator
    intentionally re-uploading the exact same bytes later (new caption,
    new schedule, new campaign) is a distinct record, not a duplicate to
    collapse — videos.id is the real identity; file_hash is a reusable
    fingerprint, no longer a uniqueness constraint."""
    from content_automation.media.inspection import file_hash

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path)
    h = file_hash(upload_path)

    first = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )
    second = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip-retry.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert second.id != first.id  # two distinct records, not a dedup
    assert second.file_hash == first.file_hash  # same reusable content fingerprint
    assert storage.exists(first.storage_key)
    assert storage.exists(second.storage_key)
    assert first.storage_key != second.storage_key  # each has its own tenant-scoped key

    # metadata/scheduling fields diverge independently — updating one never
    # touches the other, even though they share a file_hash.
    store.update_video(first.id, status="ASSIGNED", caption_text="Iteration 1 caption")
    store.update_video(second.id, status="DISCOVERED")
    refreshed_first = store.get_video(first.id)
    refreshed_second = store.get_video(second.id)
    assert refreshed_first.status == "ASSIGNED" and refreshed_first.caption_text == "Iteration 1 caption"
    assert refreshed_second.status == "DISCOVERED" and refreshed_second.caption_text is None


def test_create_video_from_upload_creates_a_distinct_record_for_a_different_users_identical_content(store, storage, tmp_path):
    """The cross-tenant mirror of the same-user case above — two different
    users uploading byte-identical content each get their own record; this
    is no longer rejected (there is no more DuplicateVideoContentError)."""
    from content_automation.media.inspection import file_hash

    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path)
    h = file_hash(upload_path)

    video_a = media_storage.create_video_from_upload(
        store, storage, user_a.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )
    video_b = media_storage.create_video_from_upload(
        store, storage, user_b.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert video_a.id != video_b.id
    assert video_a.user_id == user_a.id and video_b.user_id == user_b.id  # tenant isolation preserved
    assert video_a.file_hash == video_b.file_hash == h


class _FakeHostedStorage:
    """A stub StorageProtocol reporting provider_name="supabase" while
    delegating actual bytes to a real LocalStorage underneath — proves
    create_video_from_upload stamps storage_provider from whatever backend
    is *actually injected*, never a hardcoded string, without a real
    Supabase account (matches this module's own "backend-agnostic, real
    Supabase covered separately in test_storage_supabase.py" framing).
    Milestone 3.7 follow-up's storage_provider="local" investigation."""

    provider_name = "supabase"

    def __init__(self, delegate):
        self._delegate = delegate

    def put(self, key, local_path):
        self._delegate.put(key, local_path)

    def exists(self, key):
        return self._delegate.exists(key)

    def delete(self, key):
        self._delegate.delete(key)

    def materialize(self, key):
        return self._delegate.materialize(key)


def test_create_video_from_upload_records_the_actual_injected_backend_not_a_hardcoded_string(store, tmp_path):
    from content_automation.media.inspection import file_hash
    from content_automation.storage.local import LocalStorage

    hosted_storage = _FakeHostedStorage(LocalStorage(root=tmp_path / "objects"))
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path)

    video = media_storage.create_video_from_upload(
        store, hosted_storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=file_hash(upload_path), file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert video.storage_provider == "supabase"  # not "local", even though bytes are stored locally under the hood


def test_create_video_from_upload_never_touches_the_original_upload_path(store, storage, tmp_path):
    """The caller (api/routes/videos.py) owns cleanup of its own temp
    file — this function must never delete or move the source it was
    handed, only copy from it (matches StorageProtocol.put's own
    contract)."""
    from content_automation.media.inspection import file_hash

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path)

    media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=file_hash(upload_path), file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert upload_path.exists()


# ---------------------------------------------------------------------------
# delete_video (Milestone 3.7 follow-up — Delete Video)
# ---------------------------------------------------------------------------

def _hosted_video(store, storage, tmp_path, user_id, *, name="clip", content=b"upload bytes"):
    from content_automation.media.inspection import file_hash

    upload_path = _upload_tmp_file(tmp_path, name=name, content=content)
    return media_storage.create_video_from_upload(
        store, storage, user_id, local_path=upload_path, original_filename=f"{name}.mp4",
        file_hash=file_hash(upload_path), file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )


def test_delete_video_removes_the_row_and_the_stored_object(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _hosted_video(store, storage, tmp_path, user.id)
    assert storage.exists(video.storage_key)

    media_storage.delete_video(store, storage, video.id, user.id)

    assert store.get_video(video.id) is None
    assert not storage.exists(video.storage_key)


def test_deleting_one_duplicate_hash_video_leaves_the_other_completely_untouched(store, storage, tmp_path):
    """File-hash-architecture requirement: two rows sharing a file_hash are
    independent records — deleting one must never affect the other's DB
    row or its own stored object."""
    from content_automation.media.inspection import file_hash

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path, content=b"shared duplicate bytes")
    h = file_hash(upload_path)

    first = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="first.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )
    second = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="second.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    media_storage.delete_video(store, storage, first.id, user.id)

    assert store.get_video(first.id) is None
    assert not storage.exists(first.storage_key)
    still_there = store.get_video(second.id)
    assert still_there is not None
    assert still_there.file_hash == h
    assert storage.exists(second.storage_key)


def test_delete_video_rejects_wrong_user(store, storage, tmp_path):
    user_a = store.create_user("a@example.com", "A", NOW.isoformat())
    user_b = store.create_user("b@example.com", "B", NOW.isoformat())
    video = _hosted_video(store, storage, tmp_path, user_a.id)

    with pytest.raises(media_storage.MediaOwnershipError):
        media_storage.delete_video(store, storage, video.id, user_b.id)

    assert store.get_video(video.id) is not None  # untouched


def test_delete_video_rejects_a_nonexistent_video(store, storage):
    user = store.create_user("a@example.com", "A", NOW.isoformat())

    with pytest.raises(media_storage.MediaOwnershipError):
        media_storage.delete_video(store, storage, 999999, user.id)


def test_delete_video_refuses_when_assigned_to_a_slot(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _hosted_video(store, storage, tmp_path, user.id)
    store.insert_slot_if_missing("2026-09-30T12:00:00", "pillar", "prompt", NOW.isoformat(), user_id=user.id)
    slot = store.find_earliest_open_slot_fifo(NOW.isoformat(), user_id=user.id)
    store.assign_slot(video.id, slot.id)

    with pytest.raises(media_storage.VideoHasScheduleReferencesError):
        media_storage.delete_video(store, storage, video.id, user.id)

    assert store.get_video(video.id) is not None  # untouched, and still stored
    assert storage.exists(video.storage_key)


def test_delete_video_refuses_when_a_platform_post_exists(store, storage, tmp_path):
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _hosted_video(store, storage, tmp_path, user.id)
    store.insert_platform_post(video.id, "tiktok", NOW.isoformat(), user_id=user.id)

    with pytest.raises(media_storage.VideoHasScheduleReferencesError):
        media_storage.delete_video(store, storage, video.id, user.id)

    assert store.get_video(video.id) is not None


def test_delete_video_never_touches_a_legacy_local_video_with_no_storage_key(store, tmp_path):
    """A video that only ever went through the local CLI pipeline
    (canonical_media_path set, storage_provider/storage_key NULL) has
    nothing StorageProtocol knows how to delete — deleting it must not
    call storage.delete at all, let alone touch the local file (see
    ADR-0009 "Retention"). Uses a storage stub that raises if ever
    called, to prove that path is genuinely skipped, not just
    coincidentally successful."""

    class _ExplodingStorage:
        provider_name = "local"

        def delete(self, key):
            raise AssertionError(f"storage.delete should never be called for a legacy video (key={key!r})")

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video, video_path = _video_with_local_media(store, tmp_path, user.id)

    media_storage.delete_video(store, _ExplodingStorage(), video.id, user.id)

    assert store.get_video(video.id) is None
    assert video_path.exists()  # local file itself is out of scope — never deleted


def test_delete_video_nulls_out_upload_attempt_video_id_but_keeps_the_attempt_row(store, storage, tmp_path):
    """upload_attempts is per-file telemetry, not per-video state — see
    ContentStore.delete_video's docstring. Deleting the video must not
    erase the historical record that this attempt happened, succeeded,
    and took however long it took; it only clears the now-dangling
    video_id reference."""
    user = store.create_user("a@example.com", "A", NOW.isoformat())
    video = _hosted_video(store, storage, tmp_path, user.id)
    batch = store.create_upload_batch(user.id, started_at=NOW.isoformat(), file_count=1)
    attempt = store.create_upload_attempt(batch.id, user.id, "clip.mp4", started_at=NOW.isoformat())
    store.update_upload_attempt(attempt.id, status="SUCCESS", video_id=video.id, completed_at=NOW.isoformat())

    media_storage.delete_video(store, storage, video.id, user.id)

    [remaining] = store.get_upload_attempts_for_batch(batch.id)
    assert remaining.id == attempt.id
    assert remaining.status == "SUCCESS"  # historical outcome preserved
    assert remaining.video_id is None  # no longer dangling


def test_delete_video_then_re_upload_the_same_bytes_produces_a_clean_new_record(store, storage, tmp_path):
    """Delete-then-reupload works cleanly: the deleted video's own storage
    object is actually gone, and the fresh upload gets its own storage
    object under its own (new) video id — re-uploading identical bytes was
    already unconditionally allowed even without deleting anything first
    (see the re-upload-architecture tests above); this test's own value is
    proving delete doesn't leave anything behind that a subsequent upload
    could collide with or resurrect."""
    from content_automation.media.inspection import file_hash

    user = store.create_user("a@example.com", "A", NOW.isoformat())
    upload_path = _upload_tmp_file(tmp_path, content=b"identical bytes")
    h = file_hash(upload_path)

    first = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )
    first_storage_key = first.storage_key
    media_storage.delete_video(store, storage, first.id, user.id)
    assert not storage.exists(first_storage_key)

    second = media_storage.create_video_from_upload(
        store, storage, user.id, local_path=upload_path, original_filename="clip.mp4",
        file_hash=h, file_size_bytes=upload_path.stat().st_size, created_at=NOW.isoformat(),
    )

    assert second.id != first.id  # a genuinely new row, not the (deleted) original
    assert store.get_video(second.id) is not None
    assert storage.exists(second.storage_key)
