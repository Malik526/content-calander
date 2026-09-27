"""Response schemas for /api/videos (Milestone 3.7). Deliberately narrow —
same "explicit response model, never return.__dict__" convention as
schemas/me.py: a VideoRecord carries many internal fields (canonical_media_path,
storage_key, classification internals, ...) that are either meaningless for
a freshly-uploaded video, server-filesystem-internal, or simply not
something the frontend needs — see api/routes/videos.py's own note on why
storage_key/storage_provider stay out of this response, mirroring the
Milestone 3.6 security review's "don't surface an opaque internal
identifier with no user value" lesson (TikTok's open_id)."""

from pydantic import BaseModel


class VideoResponse(BaseModel):
    id: int
    original_filename: str
    status: str
    file_size_bytes: int | None
    created_at: str
    # Milestone 3.9 (Queue + Calendar Functionality): whether this video
    # already occupies a content_slot — the Library/assign UI needs this to
    # know which of the caller's videos are eligible to assign to a slot.
    # Unlike storage_key/storage_provider (deliberately withheld — see this
    # file's own module docstring), a video's own slot id has real,
    # user-facing meaning and isn't an opaque internal identifier.
    assigned_slot_id: int | None


class VideoListResponse(BaseModel):
    videos: list[VideoResponse]


class VideoUploadResult(BaseModel):
    """One outcome per file in a batch upload — Phase 2's requirement 5
    ("handle per-file success/failure cleanly"): a batch is never
    all-or-nothing, so the response always describes every file's own
    result rather than failing the whole request for one bad file."""

    filename: str
    success: bool
    video: VideoResponse | None = None
    error: str | None = None


class VideoUploadBatchResponse(BaseModel):
    results: list[VideoUploadResult]
