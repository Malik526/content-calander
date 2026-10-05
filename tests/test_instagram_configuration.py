"""Tests for the Instagram configuration contract (Milestone 4.0): verified
scopes and validation that names problems without echoing values."""

import pytest

from content_automation import config
from content_automation.publishing.instagram import configuration

VALID = {
    "INSTAGRAM_APP_ID": "1234567890123456",
    "INSTAGRAM_APP_SECRET": "not-a-real-secret-value",
    "INSTAGRAM_REDIRECT_URI": "https://api.example.com/api/platforms/instagram/callback",
    "INSTAGRAM_MEDIA_URL_TTL_SECONDS": 3600,
}


@pytest.fixture
def configure(monkeypatch):
    def apply(**overrides):
        for name, value in {**VALID, **overrides}.items():
            monkeypatch.setattr(config, name, value)
    return apply


def test_scopes_are_the_current_instagram_login_publishing_scopes():
    assert configuration.SCOPES == ("instagram_business_basic", "instagram_business_content_publish")
    assert not any(scope.startswith("business_") for scope in configuration.SCOPES)  # names deprecated 2025-01-27


def test_a_complete_configuration_has_no_problems(configure):
    configure()
    assert configuration.configuration_problems() == []
    assert configuration.is_configured()


def test_unset_configuration_lists_every_missing_variable(configure):
    configure(INSTAGRAM_APP_ID="", INSTAGRAM_APP_SECRET="", INSTAGRAM_REDIRECT_URI="")
    problems = configuration.configuration_problems()
    assert problems == ["INSTAGRAM_APP_ID is not set", "INSTAGRAM_APP_SECRET is not set", "INSTAGRAM_REDIRECT_URI is not set"]
    assert not configuration.is_configured()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"INSTAGRAM_APP_ID": "my-app"}, "INSTAGRAM_APP_ID must be the numeric Instagram app ID"),
        ({"INSTAGRAM_REDIRECT_URI": "http://api.example.com/cb"}, "INSTAGRAM_REDIRECT_URI must be an absolute https URL"),
        ({"INSTAGRAM_REDIRECT_URI": "/api/platforms/instagram/callback"}, "INSTAGRAM_REDIRECT_URI must be an absolute https URL"),
        ({"INSTAGRAM_MEDIA_URL_TTL_SECONDS": 60}, "INSTAGRAM_MEDIA_URL_TTL_SECONDS must be between 300 and 86400"),
        ({"INSTAGRAM_MEDIA_URL_TTL_SECONDS": 7 * 86_400}, "INSTAGRAM_MEDIA_URL_TTL_SECONDS must be between 300 and 86400"),
    ],
)
def test_invalid_values_are_reported(configure, overrides, expected):
    configure(**overrides)
    assert configuration.configuration_problems() == [expected]


def test_problems_never_include_configured_values(configure):
    configure(INSTAGRAM_APP_ID="secret-looking-id", INSTAGRAM_REDIRECT_URI="http://leaky.example.com/cb")
    joined = " ".join(configuration.configuration_problems())
    assert "secret-looking-id" not in joined and "leaky.example.com" not in joined and VALID["INSTAGRAM_APP_SECRET"] not in joined


def test_empty_optional_settings_copied_from_env_example_fall_back_to_defaults(monkeypatch):
    import importlib

    monkeypatch.setenv("INSTAGRAM_GRAPH_API_VERSION", "")
    monkeypatch.setenv("INSTAGRAM_MEDIA_URL_TTL_SECONDS", "")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.INSTAGRAM_GRAPH_API_VERSION == "v25.0"
        assert reloaded.INSTAGRAM_MEDIA_URL_TTL_SECONDS == 3600
    finally:
        monkeypatch.undo()
        importlib.reload(config)
