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
    # Milestone 3.14 follow-up: "@username", else the nickname, else None.
    account_label: str | None = None
    # Milestone 3.14 follow-up: the connected account's identity, from
    # TikTok's creator_info (publishing/tiktok/creator_identity.py). All
    # None when not connected or when the lookup fails — connected is
    # unaffected either way.
    creator_username: str | None = None
    creator_nickname: str | None = None
    creator_avatar_url: str | None = None


class TikTokConnectStartResponse(BaseModel):
    authorization_url: str
