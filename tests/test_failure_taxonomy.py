"""Tests for publishing.failure_taxonomy (Milestone 3.11) — stored failure
codes map to fixed, sanitized categories/copy; unknown or missing codes fall
back to UNKNOWN_ERROR; output never echoes its input."""

import pytest

from content_automation.publishing import failure_taxonomy as ft


@pytest.mark.parametrize(
    "code, category, hint",
    [
        ("REAUTHORIZATION_REQUIRED", "AUTH_REQUIRED", "RECONNECT_ACCOUNT"),
        ("AUTH_ERROR", "AUTH_REQUIRED", "RECONNECT_ACCOUNT"),
        ("access_token_invalid", "AUTH_REQUIRED", "RECONNECT_ACCOUNT"),
        ("auth_removed", "AUTH_REQUIRED", "RECONNECT_ACCOUNT"),
        ("CAPTION_TOO_LONG", "CAPTION_INVALID", "EDIT_CAPTION"),
        ("CAPTION_MISSING", "CAPTION_INVALID", "EDIT_CAPTION"),
        ("CORRUPT_MEDIA", "MEDIA_INVALID", None),
        ("MEDIA_INCOMPATIBLE", "MEDIA_INVALID", None),
        ("duration_check_failed", "MEDIA_INVALID", None),
        ("LOCAL_FILE_MISSING", "MEDIA_UNAVAILABLE", None),
        ("STORAGE_UNAVAILABLE", "MEDIA_UNAVAILABLE", None),
        ("UNAUDITED_CLIENT_PRIVACY_RESTRICTION", "PLATFORM_REJECTED", None),
        ("PLATFORM_REPORTED_FAILURE", "PLATFORM_REJECTED", None),
        ("rate_limit_exceeded", "RATE_LIMITED", "TRY_AGAIN_LATER"),
        ("spam_risk_too_many_posts", "RATE_LIMITED", "TRY_AGAIN_LATER"),
        ("MALFORMED_RESPONSE", "TEMPORARY_PLATFORM_ERROR", "TRY_AGAIN_LATER"),
        ("AUTH_HTTP_ERROR", "TEMPORARY_PLATFORM_ERROR", "TRY_AGAIN_LATER"),
        ("internal", "TEMPORARY_PLATFORM_ERROR", "TRY_AGAIN_LATER"),
        ("NETWORK_ERROR", "NETWORK_ERROR", "TRY_AGAIN_LATER"),
        ("UPLOAD_NETWORK_ERROR", "NETWORK_ERROR", "TRY_AGAIN_LATER"),
        ("HTTP_ERROR", "UNKNOWN_ERROR", None),
        ("PUBLISH_FAILED", "UNKNOWN_ERROR", None),
        ("some_new_tiktok_code", "UNKNOWN_ERROR", None),
        (None, "UNKNOWN_ERROR", None),
    ],
)
def test_codes_map_to_categories_and_hints(code, category, hint):
    description = ft.describe_failure("tiktok", code)
    assert description.category == category
    assert description.action_hint == hint


def test_specific_messages():
    assert ft.describe_failure("tiktok", "REAUTHORIZATION_REQUIRED").message == (
        "TikTok connection needs to be renewed. Reconnect your account in Settings."
    )
    assert ft.describe_failure("tiktok", "CAPTION_TOO_LONG").message == "Caption is too long for TikTok."
    assert ft.describe_failure("tiktok", "rate_limit_exceeded").message == (
        "TikTok is limiting how often this account can post right now. Try again later."
    )


def test_platform_codes_do_not_leak_across_platforms():
    # A TikTok-only code means nothing for another platform.
    assert ft.categorize_failure("instagram", "auth_removed") == "UNKNOWN_ERROR"
    assert ft.categorize_failure("instagram", "NETWORK_ERROR") == "NETWORK_ERROR"


@pytest.mark.parametrize(
    "hostile_code",
    [
        "TikTok HTTP 500: {'request_id': 'abc123', 'access_token': 'secret'}",
        "Traceback (most recent call last):\n  File \"x.py\"",
        "<script>alert(1)</script>",
    ],
)
def test_output_never_echoes_the_stored_code(hostile_code):
    description = ft.describe_failure("tiktok", hostile_code)
    assert description.category == "UNKNOWN_ERROR"
    assert hostile_code not in description.message
    assert "request_id" not in description.message
    assert "secret" not in description.message


def test_every_category_renders():
    for category in ft._CATEGORY_COPY:
        template, _ = ft._CATEGORY_COPY[category]
        assert "{" not in template.format(platform="TikTok")
