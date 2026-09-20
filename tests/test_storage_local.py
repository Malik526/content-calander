"""Tests for storage.local.LocalStorage — the dev/test StorageProtocol
implementation (Milestone 3.4). No network dependency."""

import pytest

from content_automation.storage.local import LocalStorage, StorageObjectNotFoundError


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(root=tmp_path / "objects")


def test_put_then_exists(storage, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video bytes")

    assert storage.exists("users/1/videos/1/source.mp4") is False
    storage.put("users/1/videos/1/source.mp4", src)
    assert storage.exists("users/1/videos/1/source.mp4") is True


def test_put_is_upsert(storage, tmp_path):
    src1 = tmp_path / "v1.mp4"
    src1.write_bytes(b"version one")
    src2 = tmp_path / "v2.mp4"
    src2.write_bytes(b"version two, longer content")

    storage.put("k", src1)
    storage.put("k", src2)  # overwrite, not a duplicate/error

    with storage.materialize("k") as path:
        assert path.read_bytes() == b"version two, longer content"


def test_materialize_yields_matching_bytes(storage, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"exact bytes" * 1000)
    storage.put("k", src)

    with storage.materialize("k") as materialized:
        assert materialized.read_bytes() == src.read_bytes()
        assert materialized != src  # a real separate temp copy, not the stored path itself


def test_materialize_cleans_up_temp_file_on_success(storage, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"bytes")
    storage.put("k", src)

    with storage.materialize("k") as materialized:
        assert materialized.exists()
    assert not materialized.exists()


def test_materialize_cleans_up_temp_file_on_exception(storage, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"bytes")
    storage.put("k", src)

    materialized_path = None
    with pytest.raises(RuntimeError):
        with storage.materialize("k") as materialized:
            materialized_path = materialized
            raise RuntimeError("simulated failure mid-use")
    assert not materialized_path.exists()


def test_materialize_missing_key_raises(storage):
    with pytest.raises(StorageObjectNotFoundError):
        with storage.materialize("does/not/exist"):
            pass


def test_delete_is_idempotent(storage, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"bytes")
    storage.put("k", src)

    storage.delete("k")
    assert storage.exists("k") is False
    storage.delete("k")  # second delete of an already-gone key must not raise


def test_delete_missing_key_does_not_raise(storage):
    storage.delete("never/existed")  # must not raise


def test_provider_name(storage):
    assert storage.provider_name == "local"
