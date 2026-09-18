"""Unit tests for retry_classification.py."""

import pytest

from content_automation.scheduling import retry_classification as rc
from content_automation.publishing.publisher import PublishError


@pytest.mark.parametrize("reason_code", ["NETWORK_ERROR", "UPLOAD_NETWORK_ERROR"])
def test_known_transient_codes_are_retryable(reason_code):
    assert rc.is_retryable(reason_code) is True


@pytest.mark.parametrize(
    "reason_code",
    [
        "LOCAL_FILE_MISSING", "CAPTION_TOO_LONG", "CORRUPT_MEDIA", "VIDEO_TOO_LONG",
        "SELF_ONLY_UNAVAILABLE", "UNSUPPORTED_PRIVACY_LEVEL",
        "UNAUDITED_CLIENT_PRIVACY_RESTRICTION", "AUTH_ERROR",
    ],
)
def test_known_permanent_codes_are_terminal(reason_code):
    assert rc.is_retryable(reason_code) is False


def test_http_5xx_is_retryable_when_reason_code_is_unrecognized():
    assert rc.is_retryable("HTTP_ERROR", http_status=503) is True


def test_http_4xx_is_terminal_when_reason_code_is_unrecognized():
    assert rc.is_retryable("HTTP_ERROR", http_status=404) is False


def test_unrecognized_code_with_no_http_status_defaults_terminal():
    """A dynamic real TikTok API error code this codebase has no
    documented knowledge of (TIKTOK_API_ERROR's fallback can carry
    anything TikTok chooses to send) must fail closed, not be retried
    blindly."""
    assert rc.is_retryable("some_tiktok_specific_error_code_we_dont_know") is False


def test_known_terminal_code_overrides_http_status():
    """The explicit reason_code lists take priority over http_status —
    e.g. CAPTION_TOO_LONG must stay terminal even if it somehow carried a
    5xx (it never does in practice, but the allowlist must win either way)."""
    assert rc.is_retryable("CAPTION_TOO_LONG", http_status=503) is False


def test_known_retryable_code_overrides_http_status():
    assert rc.is_retryable("NETWORK_ERROR", http_status=404) is True


def test_classify_reads_reason_code_and_http_status_from_a_real_error():
    error = PublishError("upload rejected", reason_code="UPLOAD_FAILED", http_status=500)
    assert rc.classify(error) is True

    error = PublishError("upload rejected", reason_code="UPLOAD_FAILED", http_status=400)
    assert rc.classify(error) is False


def test_classify_handles_an_error_with_no_http_status():
    error = PublishError("network down", reason_code="NETWORK_ERROR")
    assert rc.classify(error) is True
