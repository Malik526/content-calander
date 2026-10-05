"""
routes/platforms_instagram.py — Instagram connection status (Milestone 4.0).

What it does:
  GET /api/platforms/instagram/status reports the caller's real Instagram
  connection state from the shared, platform-neutral tables
  (platform_connections + platform_credentials, the same ones TikTok uses)
  and whether connecting is available. Read-only: there is no connect,
  callback or disconnect route until Milestone 4.1, so connect_available is
  False even when the server is configured. Owner-scoped like every
  protected route — the user comes only from the verified bearer token.

Dependencies:
  api.dependencies.auth, api.schemas.platforms, publishing.platforms,
  publishing.instagram.configuration.
"""

from fastapi import APIRouter, Depends

from content_automation.api.dependencies.auth import get_current_user, get_store
from content_automation.api.schemas.platforms import PlatformConnectionStatus
from content_automation.persistence.content_store import UserRecord
from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.instagram.configuration import is_configured
from content_automation.publishing.platforms import INSTAGRAM, get_platform

router = APIRouter()


@router.get("/platforms/instagram/status", response_model=PlatformConnectionStatus)
def get_instagram_status(
    user: UserRecord = Depends(get_current_user), store: ContentStoreProtocol = Depends(get_store),
) -> PlatformConnectionStatus:
    connect_available = get_platform(INSTAGRAM).connection_available and is_configured()
    connection = store.get_platform_connection(user.id, INSTAGRAM)
    if connection is None:
        return PlatformConnectionStatus(platform=INSTAGRAM, connected=False, status="DISCONNECTED", connect_available=connect_available)
    # Same rule as TikTok: ACTIVE with a stored credential is connected;
    # an ACTIVE row without one is not.
    connected = connection.status == "ACTIVE" and store.get_platform_credential(connection.id) is not None
    status = "ACTIVE" if connected else ("DISCONNECTED" if connection.status == "ACTIVE" else connection.status)
    return PlatformConnectionStatus(platform=INSTAGRAM, connected=connected, status=status, connect_available=connect_available)
