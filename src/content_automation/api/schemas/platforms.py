"""Response schemas for /api/platforms/tiktok/*. TikTokConnectionStatus is
the one shape every status read returns — deliberately excludes
access_token/refresh_token/client_secret/any credential field (Phase 16's
explicit requirement: "Never return access token, refresh token, client
secret, raw credential blob"). See api/routes/platforms_tiktok.py."""

from pydantic import BaseModel


class TikTokConnectionStatus(BaseModel):
    platform: str = "tiktok"
    connected: bool
    status: str
    # A human-readable account name/handle, when one is available — never
    # TikTok's open_id (an opaque per-app identifier, not a real label; see
    # api/routes/platforms_tiktok.py's Milestone 3.6 security-review note).
    # Currently always None: no connected scope exposes a display name yet.
    account_label: str | None = None


class TikTokConnectStartResponse(BaseModel):
    authorization_url: str
