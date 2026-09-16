"""Tests for publisher.py: the platform-neutral interface, result
dataclasses, and build_publisher() factory. No network access."""

from pathlib import Path

import pytest

import publisher


def test_publish_error_carries_reason_code():
    exc = publisher.PublishError("boom", reason_code="UPLOAD_FAILED")
    assert str(exc) == "boom"
    assert exc.reason_code == "UPLOAD_FAILED"


def test_publish_error_defaults_reason_code():
    exc = publisher.PublishError("boom")
    assert exc.reason_code == "PUBLISH_FAILED"


def test_publish_result_defaults_raw_response_to_none():
    result = publisher.PublishResult(platform_post_id="p1", status="PROCESSING")
    assert result.raw_response is None


def test_publish_status_result_defaults():
    result = publisher.PublishStatusResult(status="PUBLISH_COMPLETE")
    assert result.failure_reason is None
    assert result.raw_response is None


def test_publisher_is_abstract_and_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        publisher.Publisher()


def test_build_publisher_returns_tiktok_publisher():
    from tiktok_publisher import TikTokPublisher

    result = publisher.build_publisher("tiktok")
    assert isinstance(result, TikTokPublisher)


def test_build_publisher_passes_through_kwargs():
    result = publisher.build_publisher("tiktok", privacy_level="SELF_ONLY")
    assert result.privacy_level == "SELF_ONLY"


def test_build_publisher_rejects_unsupported_platform():
    with pytest.raises(publisher.UnsupportedPlatformError, match="instagram"):
        publisher.build_publisher("instagram")


class _FakePublisher(publisher.Publisher):
    def publish(self, video_path: Path, caption: str) -> publisher.PublishResult:
        return publisher.PublishResult(platform_post_id="fake_1", status="PROCESSING")

    def get_status(self, platform_post_id: str) -> publisher.PublishStatusResult:
        return publisher.PublishStatusResult(status="PUBLISH_COMPLETE")


def test_publisher_subclass_implementing_both_methods_can_be_instantiated():
    fake = _FakePublisher()
    result = fake.publish(Path("/tmp/does-not-matter.mp4"), "caption")
    assert result.platform_post_id == "fake_1"
    status = fake.get_status("fake_1")
    assert status.status == "PUBLISH_COMPLETE"
