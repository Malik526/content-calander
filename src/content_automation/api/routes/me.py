"""GET /api/me — the one endpoint that answers "who is currently
authenticated" (Milestone 3.6). See api/dependencies/auth.py for how the
identity is resolved; this route only shapes the response."""

from fastapi import APIRouter, Depends

from content_automation.api.dependencies.auth import get_current_user
from content_automation.api.schemas.me import MeResponse
from content_automation.persistence.content_store import UserRecord

router = APIRouter()


@router.get("/me", response_model=MeResponse)
def get_me(user: UserRecord = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=user.id, email=user.email, display_name=user.display_name)
