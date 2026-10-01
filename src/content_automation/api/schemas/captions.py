"""Request/response schemas for /api/videos/{id}/caption (Milestone 3.10:
Caption Generation + Editing). Same "explicit response model, never return
a raw dataclass" convention as schemas/videos.py/queue.py — in particular
the transcript and raw caption_source/generation metadata are never
exposed, only the user-facing provenance label."""

from pydantic import BaseModel, Field

from content_automation.config import CAPTION_TEXT_MAX_CHARS


class CaptionResponse(BaseModel):
    video_id: int
    caption_text: str | None
    # NONE | MANUAL | GENERATED | GENERATED_EDITED
    # (media.caption_editing.caption_provenance).
    provenance: str
    # Whether POST .../caption/generate can succeed for this video right now
    # (a usable transcript exists). False for every hosted upload today.
    can_generate: bool
    # False once the video has been submitted to a platform.
    editable: bool
    # Milestone 3.10.1 — read-only structured metadata derived from
    # caption_text (media.hashtags), in caption order, duplicates kept.
    # Never an input: hashtags are only ever edited as part of caption_text.
    hashtags: list[str]


class CaptionUpdateRequest(BaseModel):
    # null or "" clears the caption.
    caption_text: str | None = Field(default=None, max_length=CAPTION_TEXT_MAX_CHARS)


class CaptionGenerateRequest(BaseModel):
    # Must be true to replace an existing caption — regeneration is never a
    # silent overwrite.
    overwrite: bool = False
