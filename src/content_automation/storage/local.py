"""
local.py — LocalStorage: filesystem-backed implementation of
StorageProtocol (Milestone 3.4).

What it does:
  Stores objects as plain files under a root directory
  (config.LOCAL_STORAGE_ROOT by default), keyed by their storage key as a
  relative path. This is the dev/test backend — mirrors
  persistence.content_store.ContentStore's role for SQLite (Milestone 3.3):
  no network dependency, fast, always available, never required to be
  configured with real credentials. Nothing in the existing ingestion
  pipeline (media/processing.py, content/incoming|processed|failed/) uses
  this — LocalStorage is the new object-storage abstraction's own local
  implementation, exercised by media/media_storage.py and by tests.

Dependencies:
  stdlib only (pathlib, shutil, tempfile).
"""

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from content_automation.config import LOCAL_STORAGE_ROOT


class StorageObjectNotFoundError(Exception):
    """Raised by materialize() (either backend) when key does not exist."""


class LocalStorage:
    provider_name = "local"

    def __init__(self, root: Path = LOCAL_STORAGE_ROOT):
        self.root = root

    def _path_for(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, local_path: Path) -> None:
        dest = self._path_for(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

    def exists(self, key: str) -> bool:
        return self._path_for(key).exists()

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    @contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        """Copies to a fresh temp file (rather than yielding the stored
        path directly) so LocalStorage's materialize() has the exact same
        contract as SupabaseStorage's — a caller can freely treat the
        yielded path as scratch space (e.g. TikTokPublisher never mutates
        it, but the contract shouldn't depend on that) without risking the
        canonical stored copy, and both backends clean up identically on
        exit."""
        source = self._path_for(key)
        if not source.exists():
            raise StorageObjectNotFoundError(f"LocalStorage: no object at key={key!r}")

        with tempfile.NamedTemporaryFile(suffix=source.suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            shutil.copy2(source, tmp_path)
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
