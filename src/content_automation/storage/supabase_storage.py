"""
supabase_storage.py — SupabaseStorage: real hosted implementation of
StorageProtocol against Supabase Storage's REST API (Milestone 3.4).

What it does:
  Plain `requests` calls against Supabase Storage's REST API
  (`{SUPABASE_URL}/storage/v1/object/...`), not the `supabase-py`/
  `storage3` SDK — `requests` is already a dependency this codebase uses
  throughout (publishing/tiktok/*), and the REST surface this module needs
  is four calls, verified directly against a real Supabase project before
  this file was written (see the Milestone 3.4 evaluation record's
  "Storage Provider" phase) rather than assumed from documentation:

    upload   POST   /storage/v1/object/{bucket}/{key}   (x-upsert: true)
    exists   HEAD   /storage/v1/object/{bucket}/{key}    (200 / 400)
    download GET    /storage/v1/object/{bucket}/{key}    (streamed)
    delete   DELETE /storage/v1/object/{bucket}          ({"prefixes": [key]})

  Every call uses the service-role key (server-side only, full access to
  private buckets, never exposed client-side — config.SUPABASE_SERVICE_ROLE_KEY,
  read from the .env variable literally named SERVICE_ROLE_KEY). Uploads
  stream from an open file handle (never `.read()`s the whole file into
  memory first); downloads stream via `iter_content` directly to a temp
  file — see Phase 27 of the Milestone 3.4 brief ("avoid implementations
  that unnecessarily read the entire object into Python memory").

Dependencies:
  requests. content_automation.config (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
  SUPABASE_STORAGE_BUCKET).
"""

import mimetypes
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import requests

from content_automation.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET, SUPABASE_URL
from content_automation.storage.local import StorageObjectNotFoundError

_REQUEST_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 300
_DOWNLOAD_TIMEOUT_SECONDS = 300
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class StorageError(Exception):
    """Raised for a Supabase Storage API failure other than "object not
    found" (which materialize() reports as StorageObjectNotFoundError,
    matching LocalStorage's contract exactly). `reason_code` mirrors
    publisher.PublishError's shape (this codebase's established pattern
    for a structured, non-string-parsed failure signal) — "NETWORK_ERROR"
    for a transport-level failure, "HTTP_ERROR" for an unexpected status."""

    def __init__(self, message: str, reason_code: str = "STORAGE_FAILED", http_status: int | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.http_status = http_status


def _require_configured() -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise StorageError(
            "SupabaseStorage requires SUPABASE_URL and a service-role key (the .env variable "
            "SERVICE_ROLE_KEY). Set both in .env, or use storage.local.LocalStorage instead — "
            "see storage.factory.build_storage() for the normal selection path.",
            reason_code="NOT_CONFIGURED",
        )


class SupabaseStorage:
    provider_name = "supabase"

    def __init__(self, base_url: str = SUPABASE_URL, service_key: str = SUPABASE_SERVICE_ROLE_KEY, bucket: str = SUPABASE_STORAGE_BUCKET):
        _require_configured()
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.bucket = bucket

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.service_key}", "apikey": self.service_key}

    def _object_url(self, key: str) -> str:
        return f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"

    def put(self, key: str, local_path: Path) -> None:
        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        try:
            with local_path.open("rb") as f:
                response = requests.post(
                    self._object_url(key),
                    headers={**self._headers(), "x-upsert": "true", "Content-Type": content_type},
                    data=f,  # streamed from the open file handle, never fully read into memory first
                    timeout=_UPLOAD_TIMEOUT_SECONDS,
                )
        except requests.RequestException as exc:
            raise StorageError(f"Could not reach Supabase Storage to upload key={key!r}: {exc}", reason_code="NETWORK_ERROR") from exc
        if response.status_code >= 400:
            raise StorageError(
                f"Supabase Storage upload failed for key={key!r}: HTTP {response.status_code}: {response.text[:200]!r}",
                reason_code="HTTP_ERROR", http_status=response.status_code,
            )

    def exists(self, key: str) -> bool:
        try:
            response = requests.head(self._object_url(key), headers=self._headers(), timeout=_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise StorageError(f"Could not reach Supabase Storage to check key={key!r}: {exc}", reason_code="NETWORK_ERROR") from exc
        return response.status_code == 200

    def delete(self, key: str) -> None:
        try:
            response = requests.delete(
                f"{self.base_url}/storage/v1/object/{self.bucket}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"prefixes": [key]},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise StorageError(f"Could not reach Supabase Storage to delete key={key!r}: {exc}", reason_code="NETWORK_ERROR") from exc
        if response.status_code >= 400:
            raise StorageError(
                f"Supabase Storage delete failed for key={key!r}: HTTP {response.status_code}: {response.text[:200]!r}",
                reason_code="HTTP_ERROR", http_status=response.status_code,
            )

    @contextmanager
    def materialize(self, key: str) -> Iterator[Path]:
        if not self.exists(key):
            raise StorageObjectNotFoundError(f"SupabaseStorage: no object at bucket={self.bucket!r} key={key!r}")

        suffix = Path(key).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            try:
                with requests.get(
                    self._object_url(key), headers=self._headers(), stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                ) as response:
                    if response.status_code >= 400:
                        raise StorageError(
                            f"Supabase Storage download failed for key={key!r}: HTTP {response.status_code}",
                            reason_code="HTTP_ERROR", http_status=response.status_code,
                        )
                    with tmp_path.open("wb") as f:
                        for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                            f.write(chunk)
            except requests.RequestException as exc:
                raise StorageError(f"Could not reach Supabase Storage to download key={key!r}: {exc}", reason_code="NETWORK_ERROR") from exc
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
