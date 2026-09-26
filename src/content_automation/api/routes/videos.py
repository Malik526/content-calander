"""
routes/videos.py — hosted batch video upload + Library listing
(Milestone 3.7).

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

  GET /api/videos — the current authenticated user's own videos, newest
  first (ContentStoreProtocol.list_videos_for_user), for the Library page.
  Never accepts a user_id from the caller — exactly like every other
  protected route (see api/dependencies/auth.py).

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
  without this endpoint's contract changing.

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

Dependencies:
  content_automation.media.media_storage (create_video_from_upload,
  DuplicateVideoContentError), content_automation.media.inspection
  (file_hash), content_automation.api.dependencies.auth/storage,
  content_automation.config (SUPPORTED_VIDEO_EXTENSIONS).
"""

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile

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


def _process_one_upload(
    store: ContentStoreProtocol, storage: StorageProtocol, user_id: int, tmp_path: Path, original_filename: str,
) -> VideoUploadResult:
    """Always returns a VideoUploadResult; never raises, so the calling
    loop never has to guess which exceptions are "expected" for this one
    file vs. a real bug."""
    try:
        created_at = datetime.now(timezone.utc).isoformat()
        video = media_storage.create_video_from_upload(
            store, storage, user_id, local_path=tmp_path, original_filename=original_filename,
            file_hash=file_hash(tmp_path), file_size_bytes=tmp_path.stat().st_size, created_at=created_at,
        )
        return VideoUploadResult(filename=original_filename, success=True, video=_to_video_response(video))
    except media_storage.DuplicateVideoContentError:
        # Deliberately does not say who owns it or which video it matches —
        # see DuplicateVideoContentError's own docstring.
        return VideoUploadResult(
            filename=original_filename, success=False, error="This exact video has already been uploaded.",
        )
    except Exception as exc:  # noqa: BLE001 — see module docstring: one file's failure must never
        # abort the batch, so every exception this file's processing could
        # raise (a storage.StorageError, a DB error, anything else) is
        # turned into a per-file result here, not left to propagate and
        # 500 the whole request for every other file already/still queued.
        return VideoUploadResult(filename=original_filename, success=False, error=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/videos", response_model=VideoUploadBatchResponse)
def upload_videos(
    files: list[UploadFile] = File(...),
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
    storage: StorageProtocol = Depends(get_storage),
) -> VideoUploadBatchResponse:
    results: list[VideoUploadResult] = []
    for upload in files:
        original_filename = upload.filename or "unnamed"
        suffix = Path(original_filename).suffix.lower()
        if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
            upload.file.close()
            results.append(VideoUploadResult(
                filename=original_filename, success=False,
                error=f"Unsupported file type {suffix or '(none)'!r}. Supported: {', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}.",
            ))
            continue

        tmp_path = _save_upload_to_temp(upload, suffix)
        upload.file.close()
        results.append(_process_one_upload(store, storage, user.id, tmp_path, original_filename))

    return VideoUploadBatchResponse(results=results)


@router.get("/videos", response_model=VideoListResponse)
def list_videos(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> VideoListResponse:
    videos = store.list_videos_for_user(user.id)
    return VideoListResponse(videos=[_to_video_response(v) for v in videos])
