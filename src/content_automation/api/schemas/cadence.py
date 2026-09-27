"""Request/response schemas for /api/cadence (Milestone 3.8). Same
"explicit response model, never return a raw dataclass" convention as
schemas/videos.py — CadenceRecord/SlotRecord carry internal fields
(cadence_id linkage, pillar_key, google_calendar_event_id, ...) that
either don't apply to a hosted cadence-generated slot or aren't something
the frontend needs."""

from pydantic import BaseModel


class PostingTimeSchema(BaseModel):
    weekday: str
    posting_time: str  # "HH:MM"


class CadenceRequest(BaseModel):
    timezone: str
    is_active: bool = True
    posting_times: list[PostingTimeSchema]


class CadenceResponse(BaseModel):
    configured: bool
    timezone: str | None
    is_active: bool
    posting_times: list[PostingTimeSchema]


class SlotResponse(BaseModel):
    id: int
    scheduled_at: str
    status: str
    timezone: str | None


class SlotListResponse(BaseModel):
    slots: list[SlotResponse]
