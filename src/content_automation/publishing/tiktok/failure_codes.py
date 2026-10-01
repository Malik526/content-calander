"""
failure_codes.py — TikTok-specific failure codes -> platform-neutral failure
categories (Milestone 3.11: User-Facing Publish States + Errors).

What it does:
  TIKTOK_FAILURE_CATEGORIES maps codes that only TikTok produces onto the
  categories in publishing/failure_taxonomy.py. Two kinds of TikTok code
  reach platform_posts.failure_code:

  - Content Posting API error codes (body["error"]["code"]), which
    publishing/tiktok/publisher._parse_response passes through verbatim
    as PublishError.reason_code.
  - publish/status/fetch `fail_reason` values for a submission TikTok
    accepted and then failed (publish_tiktok._resolve_poll_outcome).

  Both lists come from TikTok's public Content Posting API documentation
  and have not all been observed live. Any code not listed here falls back
  to the generic UNKNOWN_ERROR category — never guessed from text. Codes
  this codebase raises itself (AUTH_ERROR, CAPTION_TOO_LONG,
  NETWORK_ERROR, ...) are platform-neutral and live in failure_taxonomy.py,
  not here.

Dependencies:
  none (plain data).
"""

TIKTOK_FAILURE_CATEGORIES: dict[str, str] = {
    # -- API error codes ---------------------------------------------------
    "access_token_invalid": "AUTH_REQUIRED",
    "scope_not_authorized": "AUTH_REQUIRED",
    "scope_permission_missed": "AUTH_REQUIRED",
    "rate_limit_exceeded": "RATE_LIMITED",
    "spam_risk_too_many_posts": "RATE_LIMITED",
    "spam_risk_too_many_pending_share": "RATE_LIMITED",
    "spam_risk_user_banned_from_posting": "PLATFORM_REJECTED",
    "reached_active_user_cap": "PLATFORM_REJECTED",
    "unaudited_client_can_only_post_to_private_accounts": "PLATFORM_REJECTED",
    "privacy_level_option_mismatch": "PLATFORM_REJECTED",
    "internal_error": "TEMPORARY_PLATFORM_ERROR",
    # -- publish status fail_reason values ----------------------------------
    "auth_removed": "AUTH_REQUIRED",
    "file_format_check_failed": "MEDIA_INVALID",
    "duration_check_failed": "MEDIA_INVALID",
    "frame_rate_check_failed": "MEDIA_INVALID",
    "picture_size_check_failed": "MEDIA_INVALID",
    "video_pull_failed": "TEMPORARY_PLATFORM_ERROR",
    "internal": "TEMPORARY_PLATFORM_ERROR",
    "spam_risk_text": "CAPTION_INVALID",
    "spam_risk": "PLATFORM_REJECTED",
    "publish_cancelled": "PLATFORM_REJECTED",
}
