"""
factory.py — build_storage(): the one place runtime decides local vs.
hosted object storage (Milestone 3.4).

What it does:
  config.STORAGE_BACKEND == "local" (default) -> LocalStorage.
  config.STORAGE_BACKEND == "supabase" -> SupabaseStorage.

  A configured hosted backend that is missing credentials raises
  immediately (SupabaseStorage's own __init__ check) — never silently
  falls back to LocalStorage. Mirrors
  persistence.store_factory.build_content_store()'s exact philosophy for
  DATABASE_URL (Milestone 3.3): a production deployment with a broken
  storage configuration must fail loudly, not quietly start writing media
  to a local directory nobody is looking at.

  Not yet used by any CLI entry point — media/media_storage.py (the new
  upload/materialize glue this milestone adds) takes a storage instance as
  a parameter rather than constructing one itself, so callers (tests,
  cli/migrate_media_to_object_storage.py, a future scheduler) choose
  explicitly. This mirrors store_factory's own "not yet wired into any
  CLI by default" scope boundary from Milestone 3.3.

Dependencies:
  content_automation.config (STORAGE_BACKEND), storage.local, storage.protocol.
"""

from content_automation.config import STORAGE_BACKEND
from content_automation.storage.local import LocalStorage
from content_automation.storage.protocol import StorageProtocol


class UnsupportedStorageBackendError(ValueError):
    pass


def build_storage(**kwargs) -> StorageProtocol:
    """Return a LocalStorage or SupabaseStorage instance, selected by
    config.STORAGE_BACKEND. **kwargs are forwarded to whichever
    constructor is chosen (e.g. root= for LocalStorage, bucket= for
    SupabaseStorage)."""
    if STORAGE_BACKEND == "local":
        return LocalStorage(**kwargs)
    if STORAGE_BACKEND == "supabase":
        from content_automation.storage.supabase_storage import SupabaseStorage

        return SupabaseStorage(**kwargs)
    raise UnsupportedStorageBackendError(
        f"Unsupported STORAGE_BACKEND={STORAGE_BACKEND!r}. Valid values: local, supabase"
    )
