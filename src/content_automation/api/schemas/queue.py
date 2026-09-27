"""Request/response schemas for /api/queue (Milestone 3.9: Queue +
Calendar Functionality). Same "explicit response model, never return a raw
dataclass" convention as schemas/videos.py/cadence.py."""

from pydantic import BaseModel


class QueueVideoSummary(BaseModel):
    """Just enough to identify the video occupying a slot — same
    "don't surface an opaque internal identifier with no user value"
    restraint as schemas/videos.py's own VideoResponse, but id has real
    value here (it's what Assign/Remove-from-schedule act on)."""

    id: int
    original_filename: str


class QueueSlotResponse(BaseModel):
    id: int
    scheduled_at: str
    timezone: str | None
    status: str  # raw content_slots.status — only ever "OPEN" or "ASSIGNED"
    # OPEN | ASSIGNED | PUBLISHING | PUBLISHED | FAILED — computed at read
    # time from the assigned video's platform_posts row(s), since
    # content_slots.status itself is never written past ASSIGNED by any
    # code path (see api/routes/queue.py's module docstring).
    display_status: str
    assigned_video: QueueVideoSummary | None
    platform_post_status: str | None


class QueueSlotListResponse(BaseModel):
    slots: list[QueueSlotResponse]


class AssignToSlotRequest(BaseModel):
    video_id: int


class AssignNextRequest(BaseModel):
    video_id: int
