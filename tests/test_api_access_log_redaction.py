"""Tests for api.app's uvicorn access-log filter (Milestone 3.6 security
review, generalized in Milestone 4.1): the one-time OAuth `code` and
`state` are redacted on both supported callback paths, and every other
request — and every other query parameter — is logged unchanged."""

import logging

import pytest

from content_automation.api.app import _RedactOAuthCallbackQueryFilter


def _record(full_path, args_override=None):
    args = args_override if args_override is not None else ("127.0.0.1:5000", "GET", full_path, "1.1", 302)
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)


def _logged(full_path):
    record = _record(full_path)
    assert _RedactOAuthCallbackQueryFilter().filter(record) is True
    return record.getMessage()


@pytest.mark.parametrize("platform", ["tiktok", "instagram"])
def test_code_and_state_are_redacted_on_both_callback_paths(platform):
    message = _logged(f"/api/platforms/{platform}/callback?code=SECRET-CODE&state=SECRET-STATE")
    assert "SECRET-CODE" not in message and "SECRET-STATE" not in message
    assert f"/api/platforms/{platform}/callback?code=[redacted]&state=[redacted]" in message


def test_other_callback_parameters_are_kept():
    message = _logged(
        "/api/platforms/instagram/callback?error=access_denied&error_reason=user_denied&state=SECRET-STATE&error_code=200"
    )
    assert "SECRET-STATE" not in message
    assert "error=access_denied&error_reason=user_denied&state=[redacted]&error_code=200" in message


def test_a_trailing_slash_is_still_redacted():
    assert "SECRET-CODE" not in _logged("/api/platforms/instagram/callback/?code=SECRET-CODE")


def test_empty_values_are_redacted_too():
    assert "code=[redacted]" in _logged("/api/platforms/instagram/callback?code=&state=s")


@pytest.mark.parametrize(
    "path",
    [
        "/api/videos?code=keep-me&state=keep-me",
        "/api/platforms/instagram/status?state=keep-me",
        "/api/platforms/instagram/callbackextra?code=keep-me",
        "/api/queue/slots?from=2026-10-01&to=2026-10-31",
        "/api/platforms/instagram/callback",
    ],
)
def test_unrelated_requests_are_logged_unchanged(path):
    assert path in _logged(path)


def test_unexpected_record_shapes_are_left_alone():
    record = _record("", args_override=("only", "three", "args"))
    assert _RedactOAuthCallbackQueryFilter().filter(record) is True
    assert record.args == ("only", "three", "args")


def test_the_filter_is_installed_on_uvicorns_access_logger():
    filters = logging.getLogger("uvicorn.access").filters
    assert any(isinstance(f, _RedactOAuthCallbackQueryFilter) for f in filters)
