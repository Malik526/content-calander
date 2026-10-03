"""
creator_identity.py — which TikTok account a hosted connection is
authorized as (Milestone 3.14 follow-up).

What it does:
  fetch_creator_identity(store, user_id) asks TikTok's
  /v2/post/publish/creator_info/query/ (TikTokPublisher.query_creator_info —
  the same call the publisher makes before every post, under the
  video.publish scope the connection already has, so no new scope) with
  that user's stored credential, and returns only the display fields:
  creator_username, creator_nickname, creator_avatar_url.

  Milestone 3.6 deliberately never surfaced the connection's open_id (an
  opaque per-app identifier). This is the "real display name through an
  approved scope" that note was waiting for.

  Best-effort, never authoritative about the connection itself: any
  failure (TikTok unreachable, token refresh failing, malformed response)
  returns None and is logged with a code only. The caller keeps reporting
  the connection exactly as before — an identity lookup can't disconnect
  anyone. Token values and raw TikTok responses are never logged or
  returned.

Dependencies:
  publishing.tiktok.hosted_publisher, publishing.publisher (PublishError).
"""

import logging
from dataclasses import dataclass

from content_automation.persistence.protocol import ContentStoreProtocol
from content_automation.publishing.publisher import PublishError
from content_automation.publishing.tiktok.hosted_publisher import build_hosted_tiktok_publisher

logger = logging.getLogger(__name__)

# Shorter than the publisher's 30s default: this runs inside a Settings page
# request, and a slow TikTok must not hang the page — it just shows no name.
_LOOKUP_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CreatorIdentity:
    username: str | None
    nickname: str | None
    avatar_url: str | None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def fetch_creator_identity(store: ContentStoreProtocol, user_id: int) -> CreatorIdentity | None:
    """The connected TikTok account's display identity, or None when it
    can't be determined right now (see module docstring)."""
    publisher = build_hosted_tiktok_publisher(store, user_id)
    try:
        info = publisher.query_creator_info(timeout=_LOOKUP_TIMEOUT_SECONDS)
    except PublishError as exc:
        logger.info("event=tiktok_creator_info_failed user_id=%s failure_code=%s", user_id, exc.reason_code)
        return None
    except Exception as exc:  # noqa: BLE001 — a display nicety must never fail the status request
        logger.info("event=tiktok_creator_info_failed user_id=%s error_type=%s", user_id, type(exc).__name__)
        return None

    identity = CreatorIdentity(
        username=_text(info.get("creator_username")),
        nickname=_text(info.get("creator_nickname")),
        avatar_url=_text(info.get("creator_avatar_url")),
    )
    return identity if (identity.username or identity.nickname or identity.avatar_url) else None
