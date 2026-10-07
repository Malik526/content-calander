"""Tests for api.oauth_return_targets (Milestone 4.1): the server-owned,
exact-match allowlist of post-callback destinations, and appending the
callback outcome without losing a target's own query parameters."""

import pytest

from content_automation import config
from content_automation.api import oauth_return_targets as targets


@pytest.fixture(autouse=True)
def frontend(monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(config, "OAUTH_EXTRA_RETURN_TARGETS", [])


def test_the_default_is_the_web_settings_page(monkeypatch):
    assert targets.default_return_target() == "https://app.example.com/app/settings"
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", "https://app.example.com/")
    assert targets.default_return_target() == "https://app.example.com/app/settings"


@pytest.mark.parametrize("base", ["", "ftp://app.example.com", "app.example.com", "http://app.example.com"])
def test_no_default_without_a_usable_frontend(monkeypatch, base):
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", base)
    assert targets.default_return_target() is None
    with pytest.raises(targets.ReturnTargetNotAllowedError):
        targets.resolve_return_target(None)


def test_local_development_frontend_is_allowed(monkeypatch):
    monkeypatch.setattr(config, "FRONTEND_BASE_URL", "http://localhost:3000")
    assert targets.default_return_target() == "http://localhost:3000/app/settings"


def test_resolve_returns_the_default_or_an_exact_allowlisted_entry(monkeypatch):
    monkeypatch.setattr(config, "OAUTH_EXTRA_RETURN_TARGETS", ["https://app.example.com/native-return"])
    assert targets.resolve_return_target(None) == "https://app.example.com/app/settings"
    assert targets.resolve_return_target("https://app.example.com/app/settings") == "https://app.example.com/app/settings"
    assert targets.resolve_return_target("https://app.example.com/native-return") == "https://app.example.com/native-return"


@pytest.mark.parametrize(
    "requested",
    [
        "https://evil.example.net/app/settings",
        "//app.example.com/app/settings",
        "https://app.example.com.evil.net/app/settings",
        "https://evil.net/https://app.example.com/app/settings",
        "https://user@app.example.com/app/settings",
        "https://app.example.com/app/settings/",
        "https://APP.example.com/app/settings",
        "https://app.example.com/app/settings#x",
        "https://app.example.com/app",
        "/app/settings",
        "",
    ],
)
def test_resolve_rejects_anything_not_exactly_allowlisted(requested):
    with pytest.raises(targets.ReturnTargetNotAllowedError) as error:
        targets.resolve_return_target(requested)
    assert requested not in str(error.value) or requested == ""


@pytest.mark.parametrize(
    "bad_entry",
    ["javascript:alert(1)", "http://evil.example.net/cb", "https://user:pw@app.example.com/cb", "not a url", "//app.example.com/cb"],
)
def test_misconfigured_allowlist_entries_are_never_honored(monkeypatch, bad_entry):
    monkeypatch.setattr(config, "OAUTH_EXTRA_RETURN_TARGETS", [bad_entry])
    assert bad_entry not in targets.allowed_return_targets()
    assert not targets.is_allowed_return_target(bad_entry)


def test_is_allowed_handles_missing_values():
    assert not targets.is_allowed_return_target(None)
    assert not targets.is_allowed_return_target("")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("https://app.example.com/app/settings", "https://app.example.com/app/settings?instagram=connected"),
        ("https://app.example.com/app/settings?tab=platforms", "https://app.example.com/app/settings?tab=platforms&instagram=connected"),
        ("https://app.example.com/app/settings?instagram=denied&x=1", "https://app.example.com/app/settings?x=1&instagram=connected"),
        ("https://app.example.com/app/settings?flag=#top", "https://app.example.com/app/settings?flag=&instagram=connected#top"),
    ],
)
def test_with_outcome_keeps_existing_parameters_and_replaces_its_own(target, expected):
    assert targets.with_outcome(target, "instagram", "connected") == expected
