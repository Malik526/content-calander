"""
routes/captions.py — read, edit and generate a video's canonical
publishing caption (Milestone 3.10: Caption Generation + Editing).

What it does:
  GET  /api/videos/{video_id}/caption           — current caption + provenance.
  PUT  /api/videos/{video_id}/caption           — save a user-edited caption
                                                   ({caption_text}; null/"" clears).
  POST /api/videos/{video_id}/caption/generate  — generate and save a caption
                                                   ({overwrite}); 409 unless
                                                   overwrite=true when a caption
                                                   already exists.

  The caption is videos.caption_text — the canonical, per-record caption
  that publishing.caption_resolution hands to every publisher. All rules
  (provenance, explicit overwrite, locking after submission) live in
  media.caption_editing; this module only maps its errors to HTTP codes:
  CaptionLockedError / CaptionOverwriteRequiredError /
  CaptionGenerationUnavailableError -> 409.

  Ownership: same as queue.py/videos.py — get_current_user/get_store, and
  a video not owned by the caller is a 404 indistinguishable from one that
  doesn't exist. The transcript is never returned.

Dependencies:
  api.dependencies.auth, media.caption_editing, media.caption_generation.
"""

from fastapi import APIRouter, Depends, HTTPException

from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.schemas.captions import CaptionGenerateRequest, CaptionResponse, CaptionUpdateRequest
from content_automation.media.caption_editing import (
    CaptionLockedError,
    CaptionOverwriteRequiredError,
    caption_provenance,
    is_caption_locked,
    regenerate_caption,
    save_caption,
)
from content_automation.media.caption_generation import CaptionGenerationUnavailableError, can_generate_caption
from content_automation.persistence.content_store import UserRecord, VideoRecord
from content_automation.persistence.protocol import ContentStoreProtocol

router = APIRouter()


def to_caption_response(store: ContentStoreProtocol, video: VideoRecord) -> CaptionResponse:
    """Shared with routes/queue.py so a Queue item carries the same caption shape."""
    return CaptionResponse(
        video_id=video.id,
        caption_text=video.caption_text,
        provenance=caption_provenance(video),
        can_generate=can_generate_caption(video),
        editable=not is_caption_locked(store, video.id),
    )


def _get_owned_video(store: ContentStoreProtocol, video_id: int, user_id: int) -> VideoRecord:
    video = store.get_video(video_id)
    if video is None or video.user_id != user_id:
        raise HTTPException(status_code=404, detail="No video with that id.")
    return video


@router.get("/videos/{video_id}/caption", response_model=CaptionResponse)
def get_caption(
    video_id: int,
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
) -> CaptionResponse:
    return to_caption_response(store, _get_owned_video(store, video_id, user.id))


@router.put("/videos/{video_id}/caption", response_model=CaptionResponse)
def update_caption(
    video_id: int,
    body: CaptionUpdateRequest,
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
) -> CaptionResponse:
    video = _get_owned_video(store, video_id, user.id)
    try:
        updated = save_caption(store, video, body.caption_text)
    except CaptionLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return to_caption_response(store, updated)


@router.post("/videos/{video_id}/caption/generate", response_model=CaptionResponse)
def generate_caption(
    video_id: int,
    body: CaptionGenerateRequest,
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
) -> CaptionResponse:
    video = _get_owned_video(store, video_id, user.id)
    try:
        updated = regenerate_caption(store, video, overwrite=body.overwrite)
    except (CaptionLockedError, CaptionOverwriteRequiredError, CaptionGenerationUnavailableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return to_caption_response(store, updated)
