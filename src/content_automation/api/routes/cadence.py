"""
routes/cadence.py — hosted per-user posting-cadence configuration + future
slot generation (Milestone 3.8: Scheduling + Cadence Configuration).

What it does:
  GET /api/cadence         — the caller's cadence + posting times, or a
                              clear "not configured yet" empty shape.
  PUT /api/cadence         — full-replace upsert. Computes the fresh
                              config.CADENCE_GENERATION_HORIZON_DAYS-day
                              horizon (calendar.hosted_cadence) and calls
                              ContentStoreProtocol.save_cadence_and_regenerate_slots
                              once — save, stale-slot reconciliation, and
                              generation are one atomic operation (see that
                              method's own docstring for why this can never
                              be split into separate calls).
  GET /api/cadence/slots   — the caller's upcoming content_slots within
                              the horizon, for the Settings preview list.

  No assignment endpoint exists here, deliberately — this milestone is
  schema + generation only (see AGENTS.md's Milestone 3.8 scope note);
  scheduling/slot_matcher.py's existing FIFO contract is untouched.

Dependencies:
  content_automation.api.dependencies.auth (get_current_user, get_store).
  content_automation.calendar.hosted_cadence (build_generated_slots).
  content_automation.persistence.protocol (ContentStoreProtocol).
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.schemas.cadence import (
    CadenceRequest,
    CadenceResponse,
    PostingTimeSchema,
    SlotListResponse,
    SlotResponse,
)
from content_automation.calendar.cadence import ScheduleConfigError
from content_automation.calendar.hosted_cadence import build_generated_slots, validate_timezone
from content_automation.config import CADENCE_GENERATION_HORIZON_DAYS
from content_automation.persistence.content_store import CadenceRecord, UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol

router = APIRouter()


def _to_cadence_response(cadence: CadenceRecord | None) -> CadenceResponse:
    if cadence is None:
        return CadenceResponse(configured=False, timezone=None, is_active=False, posting_times=[])
    return CadenceResponse(
        configured=True,
        timezone=cadence.timezone,
        is_active=cadence.is_active,
        posting_times=[
            PostingTimeSchema(weekday=t.weekday, posting_time=t.posting_time) for t in cadence.posting_times
        ],
    )


@router.get("/cadence", response_model=CadenceResponse)
def get_cadence(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> CadenceResponse:
    return _to_cadence_response(store.get_cadence_for_user(user.id))


@router.put("/cadence", response_model=CadenceResponse)
def save_cadence(
    body: CadenceRequest,
    user: UserRecord = Depends(get_current_user),
    store: ContentStoreProtocol = Depends(get_store),
) -> CadenceResponse:
    posting_times = [(t.weekday, t.posting_time) for t in body.posting_times]
    try:
        validate_timezone(body.timezone)
        now_local = datetime.now(ZoneInfo(body.timezone))
        generated_slots = build_generated_slots(
            posting_times, body.timezone, body.is_active, now_local, CADENCE_GENERATION_HORIZON_DAYS,
        )
    except ScheduleConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cadence = store.save_cadence_and_regenerate_slots(
        user_id=user.id,
        timezone=body.timezone,
        is_active=body.is_active,
        posting_times=posting_times,
        generated_slots=generated_slots,
        now_utc_iso=datetime.now(dt_timezone.utc).isoformat(),
        now_local_iso=now_local.replace(tzinfo=None).isoformat(),
    )
    return _to_cadence_response(cadence)


@router.get("/cadence/slots", response_model=SlotListResponse)
def get_upcoming_slots(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> SlotListResponse:
    cadence = store.get_cadence_for_user(user.id)
    if cadence is None:
        return SlotListResponse(slots=[])

    now_local = datetime.now(ZoneInfo(cadence.timezone)).replace(tzinfo=None)
    from_iso = now_local.isoformat()
    to_iso = (now_local + timedelta(days=CADENCE_GENERATION_HORIZON_DAYS)).isoformat()
    slots = store.list_content_slots_for_user(user.id, from_iso, to_iso)
    return SlotListResponse(
        slots=[
            SlotResponse(id=s.id, scheduled_at=s.scheduled_at, status=s.status, timezone=s.timezone)
            for s in slots
        ]
    )
