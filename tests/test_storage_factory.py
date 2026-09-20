"""Tests for storage.factory.build_storage: local vs. hosted backend
selection (Milestone 3.4, Phase 25 — no silent hosted-to-local fallback,
mirroring persistence.store_factory's exact philosophy from Milestone 3.3)."""

import pytest

from content_automation.storage import factory
from content_automation.storage.local import LocalStorage


def test_local_backend_selects_local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(factory, "STORAGE_BACKEND", "local")
    storage = factory.build_storage(root=tmp_path)
    assert isinstance(storage, LocalStorage)


def test_supabase_backend_without_credentials_raises_not_silently_falls_back(monkeypatch):
    """A configured hosted backend with no credentials must raise — never
    silently return a LocalStorage instance instead."""
    monkeypatch.setattr(factory, "STORAGE_BACKEND", "supabase")
    import content_automation.storage.supabase_storage as supabase_storage_module

    monkeypatch.setattr(supabase_storage_module, "SUPABASE_URL", "")
    monkeypatch.setattr(supabase_storage_module, "SUPABASE_SERVICE_ROLE_KEY", "")

    with pytest.raises(supabase_storage_module.StorageError):
        factory.build_storage()


def test_unsupported_backend_raises_clear_error(monkeypatch):
    monkeypatch.setattr(factory, "STORAGE_BACKEND", "nonsense")
    with pytest.raises(factory.UnsupportedStorageBackendError):
        factory.build_storage()
