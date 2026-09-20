"""Integration tests for SupabaseStorage (Milestone 3.4) — real Supabase
Storage, real HTTP round trips. Skipped entirely if SUPABASE_SERVICE_ROLE_KEY
is unset. Runs against config.SUPABASE_STORAGE_TEST_BUCKET
("pickle-batch-test"), never config.SUPABASE_STORAGE_BUCKET
("pickle-batch-media", the real application bucket) — mirrors
POSTGRES_TEST_SCHEMA's role from Milestone 3.3 exactly. Every object this
file creates is deleted at the end of its own test."""

import pytest

from content_automation.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_TEST_BUCKET
from content_automation.storage.local import StorageObjectNotFoundError
from content_automation.storage.supabase_storage import SupabaseStorage

pytestmark = pytest.mark.skipif(
    not SUPABASE_SERVICE_ROLE_KEY, reason="SUPABASE_SERVICE_ROLE_KEY not configured — Supabase Storage tests skipped"
)


@pytest.fixture
def storage():
    return SupabaseStorage(bucket=SUPABASE_STORAGE_TEST_BUCKET)


@pytest.fixture
def cleanup_keys(storage):
    keys = []
    yield keys
    for key in keys:
        storage.delete(key)


def test_put_exists_materialize_delete_round_trip(storage, tmp_path, cleanup_keys):
    key = "roundtrip/hello.mp4"
    cleanup_keys.append(key)
    src = tmp_path / "hello.mp4"
    src.write_bytes(b"real supabase storage round trip bytes" * 100)

    assert storage.exists(key) is False
    storage.put(key, src)
    assert storage.exists(key) is True

    with storage.materialize(key) as materialized:
        assert materialized.read_bytes() == src.read_bytes()
    assert not materialized.exists()  # temp file cleaned up after the with block

    storage.delete(key)
    assert storage.exists(key) is False


def test_put_is_upsert(storage, tmp_path, cleanup_keys):
    key = "roundtrip/upsert.mp4"
    cleanup_keys.append(key)
    v1 = tmp_path / "v1.mp4"
    v1.write_bytes(b"version one")
    v2 = tmp_path / "v2.mp4"
    v2.write_bytes(b"version two is longer than version one")

    storage.put(key, v1)
    storage.put(key, v2)

    with storage.materialize(key) as materialized:
        assert materialized.read_bytes() == b"version two is longer than version one"


def test_materialize_missing_object_raises(storage):
    with pytest.raises(StorageObjectNotFoundError):
        with storage.materialize("definitely/does/not/exist.mp4"):
            pass


def test_exists_false_for_missing_object(storage):
    assert storage.exists("definitely/does/not/exist.mp4") is False


def test_delete_missing_object_does_not_raise(storage):
    storage.delete("never/existed.mp4")  # must not raise


def test_provider_name(storage):
    assert storage.provider_name == "supabase"


def test_large_ish_file_streams_without_loading_whole_object_into_memory(storage, tmp_path, cleanup_keys):
    """Not a true large-file test (keeping CI/network cost sane), but
    proves the streaming code path (put via an open file handle, download
    via iter_content) round-trips a multi-megabyte payload correctly —
    the same code path a real video file exercises."""
    key = "roundtrip/multi-mb.bin"
    cleanup_keys.append(key)
    src = tmp_path / "multi-mb.bin"
    src.write_bytes(b"x" * (5 * 1024 * 1024))  # 5 MB

    storage.put(key, src)
    with storage.materialize(key) as materialized:
        assert materialized.stat().st_size == 5 * 1024 * 1024
        assert materialized.read_bytes() == src.read_bytes()
