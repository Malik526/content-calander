"""
routes/videos.py — hosted batch video upload + Library listing
(Milestone 3.7, extended by the 3.7 follow-up: storage-provider accuracy +
upload performance instrumentation).

What it does:
  POST /api/videos — accepts one or more files in a single multipart
  request (real browser "select multiple files, upload" UX) and, for each
  one independently: validates the extension, streams it to a temp file,
  hashes it, uploads it to the configured object-storage backend, and
  creates an owned `videos` row for it — media.media_storage's new
  create_video_from_upload() does the actual storage+DB work; this route
  only handles the HTTP/temp-file mechanics and shapes each file's
  success/failure into VideoUploadResult. One file's failure never aborts
  the rest of the batch (Phase 2's own requirement) — every exception this
  route knows how to interpret is caught per-file, inside the loop.

  Also records one `upload_batches` row per request and one
  `upload_attempts` row per file (ContentStoreProtocol — Milestone 3.7
  follow-up), so real upload performance can be benchmarked before this
  architecture gets optimized. This is pure instrumentation: it never
  changes what gets returned to the caller or what the video row looks
  like, and a failure while recording telemetry never fails the upload
  itself (see _record_batch_completion's own try/except).

  storage_provider accuracy: create_video_from_upload always stamps
  storage_provider from the *actual* StorageProtocol instance injected via
  get_storage() (storage.provider_name) — never a literal string. If a
  real deployment's stored rows show storage_provider="local" for what was
  assumed to be a Supabase-backed upload, that is not a bug in this route
  or in create_video_from_upload — it means config.STORAGE_BACKEND was not
  actually set to "supabase" in that environment (build_storage() defaults
  to LocalStorage), and the bytes are genuinely sitting in
  config.LOCAL_STORAGE_ROOT on that machine, not in Supabase Storage at
  all. See api/app.py's startup diagnostic, which now logs STORAGE_BACKEND
  precisely so this is visible in a real deployment's logs immediately
  rather than discovered later by inspecting rows.

  GET /api/videos — the current authenticated user's own videos, newest
  first (ContentStoreProtocol.list_videos_for_user), for the Library page.
  Never accepts a user_id from the caller — exactly like every other
  protected route (see api/dependencies/auth.py).

  DELETE /api/videos/{video_id} — the Library's delete action (Milestone
  3.7 follow-up). All of the actual safety logic (ownership,
  queue/schedule-reference refusal, storage-object cleanup, DB row
  removal) lives in media.media_storage.delete_video; this route only
  maps its two failure modes to HTTP status codes. Deleting a video frees
  its file_hash, so the exact same file can be uploaded again afterward.

  Deliberately does NOT run media.inspection.inspect_media (ffprobe) or
  any transcription/classification/scheduling here — this milestone's own
  scope guardrail ("upload success must remain independent of
  transcription, scheduling, or publishing") and
  docs/architecture/hosted-product-boundary.md §4's synchronous/
  asynchronous API boundary both say the same thing: "upload initiation
  (accepting a file/reference and creating a videos row — not processing
  it)" is the FastAPI-appropriate synchronous piece; ffprobe/transcription
  are explicitly the *not*-synchronous ones. A future milestone can enrich
  these rows (duration, dimensions, transcript) as a background job
  without this endpoint's contract changing — see media/media_storage.py's
  module docstring for exactly which fields ffprobe could populate and
  where that belongs.

  Deliberately a plain `def` route, not `async def`, matching every other
  route in this API (me.py, platforms_tiktok.py) — this endpoint does real
  blocking I/O (SupabaseStorage.put's network call; ContentStore's SQLite
  connection is also only safe to use from a single thread at a time —
  check_same_thread's default). A sync route's dependencies and body all
  run together via FastAPI's own threadpool dispatch, which keeps the
  event loop free without this route manually reasoning about thread
  affinity itself (an async route mixing awaited I/O with a separate
  run_in_threadpool call for the DB work would risk that connection being
  handed to two different worker threads across two separate dispatches).
  Reads each upload via UploadFile.file (the underlying sync file object
  Starlette already gives every UploadFile) rather than the async
  `.read()` API, for the same reason.

  Timing boundaries (all server-side, time.monotonic() deltas — never
  wall-clock subtraction, which a system clock adjustment mid-request
  could corrupt): each upload_attempts.duration_ms spans from just before
  this route starts streaming that one file's bytes to a temp file, through
  hashing + storage.put + the DB write — i.e. everything this process does
  for that file. upload_batches.total_duration_ms spans the whole request,
  every file included. Neither is "network latency" or "upload speed" in
  the end-user sense: by the time this route function runs at all, uvicorn/
  Starlette has already fully received the multipart request body from the
  client's actual network connection — this server has no way to observe
  that transfer time directly. Throughput (bytes/sec) should be derived at
  query time from total_bytes/total_duration_ms rather than stored as its
  own column — see the module docstring's own "keep stored telemetry
  minimal" framing; a derived ratio is not durable state.

Dependencies:
  content_automation.media.media_storage (create_video_from_upload,
  DuplicateVideoContentError), content_automation.media.inspection
  (file_hash), content_automation.api.dependencies.auth/storage,
  content_automation.config (SUPPORTED_VIDEO_EXTENSIONS).
"""

import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.dependencies.storage import get_storage
from content_automation.api.schemas.videos import (
    VideoListResponse,
    VideoResponse,
    VideoUploadBatchResponse,
    VideoUploadResult,
)
from content_automation.config import SUPPORTED_VIDEO_EXTENSIONS
from content_automation.media import media_storage
from content_automation.media.inspection import file_hash
from content_automation.persistence.content_store import UserRecord, VideoRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.storage.protocol import StorageProtocol

router = APIRouter()

_UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
_UPLOAD_FAILED = "UPLOAD_FAILED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_video_response(video: VideoRecord) -> VideoResponse:
    return VideoResponse(
        id=video.id, original_filename=video.original_filename, status=video.status,
        file_size_bytes=video.file_size_bytes, created_at=video.created_at,
    )


def _save_upload_to_temp(upload: UploadFile, suffix: str) -> Path:
    """Streams the upload to a temp file (shutil.copyfileobj chunks
    internally — never buffers the whole file in memory), reading from
    UploadFile's underlying sync file object. See module docstring for why
    this is sync, not `await upload.read()`."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(upload.file, tmp)
        return Path(tmp.name)


@dataclass
class _UploadOutcome:
    """Everything one file's attempt needs, for both the API response
    (VideoUploadResult) and the upload_attempts row — kept as one object
    so neither has to re-derive the other (e.g. parsing error_code back
    out of a human-readable message string)."""

    result: VideoUploadResult
    file_size_bytes: int | None
    error_code: str | None
    video_id: int | None


def _process_one_upload(
    store: ContentStoreProtocol, storage: StorageProtocol, user_id: int, tmp_path: Path, original_filename: str,
) -> _UploadOutcome:
    """Always returns an _UploadOutcome; never raises, so the calling loop
    never has to guess which exceptions are "expected" for this one file
    vs. a real bug."""
    try:
        file_size_bytes = tmp_path.stat().st_size
        video = media_storage.create_video_from_upload(
            store, storage, user_id, local_path=tmp_path, original_filename=original_filename,
            file_hash=file_hash(tmp_path), file_size_bytes=file_size_bytes, created_at=_now_iso(),
        )
        return _UploadOutcome(
            result=VideoUploadResult(filename=original_filename, success=True, video=_to_video_response(video)),
            file_size_bytes=file_size_bytes, error_code=None, video_id=video.id,
        )
    except media_storage.DuplicateVideoContentError as exc:
        # Deliberately does not say who owns it or which video it matches —
        # see DuplicateVideoContentError's own docstring.
        return _UploadOutcome(
            result=VideoUploadResult(
                filename=original_filename, success=False, error="This exact video has already been uploaded.",
            ),
            file_size_bytes=tmp_path.stat().st_size, error_code=exc.reason_code, video_id=None,
        )
    except Exception as exc:  # noqa: BLE001 — see module docstring: one file's failure must never
        # abort the batch, so every exception this file's processing could
        # raise (a storage.StorageError, a DB error, anything else) is
        # turned into a per-file result here, not left to propagate and
        # 500 the whole request for every other file already/still queued.
        return _UploadOutcome(
            result=VideoUploadResult(filename=original_filename, success=False, error=str(exc)),
            file_size_bytes=None, error_code=getattr(exc, "reason_code", _UPLOAD_FAILED), video_id=None,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def _record_batch_completion(store: ContentStoreProtocol, batch_id: int, started_monotonic: float, total_bytes: int) -> None:
    """Telemetry only — a failure here must never surface as an upload
    failure to the caller, since every file has already been fully
    processed by the time this runs."""
    try:
        duration_ms = round((time.monotonic() - started_monotonic) * 1000)
        store.update_upload_batch(
            batch_id, completed_at=_now_iso(), total_bytes=total_bytes, total_duration_ms=duration_ms,
            status="COMPLETED",
        )
    except Exception:  # noqa: BLE001 — telemetry write failures are swallowed, not surfaced.
        pass


@router.post("/videos", response_model=VideoUploadBatchResponse)
def upload_videos(
    files: list[UploadFile] = File(...),
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
    storage: StorageProtocol = Depends(get_storage),
) -> VideoUploadBatchResponse:
    batch_started_monotonic = time.monotonic()
    batch = store.create_upload_batch(user.id, started_at=_now_iso(), file_count=len(files))

    results: list[VideoUploadResult] = []
    total_bytes = 0
    for upload in files:
        original_filename = upload.filename or "unnamed"
        suffix = Path(original_filename).suffix.lower()
        attempt_started_monotonic = time.monotonic()
        attempt = store.create_upload_attempt(batch.id, user.id, original_filename, started_at=_now_iso())

        if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
            upload.file.close()
            results.append(VideoUploadResult(
                filename=original_filename, success=False,
                error=f"Unsupported file type {suffix or '(none)'!r}. Supported: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}.",
            ))
            store.update_upload_attempt(
                attempt.id, completed_at=_now_iso(),
                duration_ms=round((time.monotonic() - attempt_started_monotonic) * 1000),
                status="FAILED", error_code=_UNSUPPORTED_FILE_TYPE,
            )
            continue

        tmp_path = _save_upload_to_temp(upload, suffix)
        upload.file.close()
        outcome = _process_one_upload(store, storage, user.id, tmp_path, original_filename)
        results.append(outcome.result)
        if outcome.file_size_bytes is not None:
            total_bytes += outcome.file_size_bytes
        store.update_upload_attempt(
            attempt.id, completed_at=_now_iso(),
            duration_ms=round((time.monotonic() - attempt_started_monotonic) * 1000),
            status="SUCCESS" if outcome.result.success else "FAILED",
            error_code=outcome.error_code, file_size_bytes=outcome.file_size_bytes, video_id=outcome.video_id,
        )

    _record_batch_completion(store, batch.id, batch_started_monotonic, total_bytes)
    return VideoUploadBatchResponse(results=results)


@router.get("/videos", response_model=VideoListResponse)
def list_videos(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> VideoListResponse:
    videos = store.list_videos_for_user(user.id)
    return VideoListResponse(videos=[_to_video_response(v) for v in videos])


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(
    video_id: int,
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
    storage: StorageProtocol = Depends(get_storage),
) -> Response:
    """The Library's authenticated delete action (Milestone 3.7
    follow-up). Ownership and queue/schedule-reference safety live in
    media.media_storage.delete_video — this route only translates its
    outcomes to HTTP:

      - MediaOwnershipError -> 404. Deliberately the same status for "no
        such video" and "not yours" — never reveals which case applies,
        matching every other ownership check in this codebase.
      - VideoHasScheduleReferencesError -> 409 Conflict, with a message
        safe to show the user directly: it names no other account, video,
        slot, or post, only "this video" (which the caller already owns
        and already knows the id of).
      - success -> 204 No Content, matching delete_platform_credential's
        own precedent of "nothing to return once the thing is gone."
    """
    try:
        media_storage.delete_video(store, storage, video_id, user.id)
    except media_storage.MediaOwnershipError:
        raise HTTPException(status_code=404, detail="No video with that id.")
    except media_storage.VideoHasScheduleReferencesError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=204)
