"""Tests for caption.py: transcript-derived caption candidates and
CAPTION_MODE validation."""

import pytest

from caption import CaptionConfigError, build_caption_from_transcript, validate_caption_mode


def test_build_caption_from_transcript_returns_none_for_empty():
    assert build_caption_from_transcript("") is None
    assert build_caption_from_transcript("   ") is None
    assert build_caption_from_transcript(None) is None


def test_build_caption_from_transcript_normalizes_whitespace():
    assert build_caption_from_transcript("Built  a\n\nnew   tool\ttoday.") == "Built a new tool today."


def test_build_caption_from_transcript_passes_through_normal_text():
    text = "This is a normal transcript sentence about building software."
    assert build_caption_from_transcript(text) == text


def test_build_caption_from_transcript_does_not_truncate_long_text():
    """No platform-specific length cap on the canonical stored caption —
    truncation belongs at the eventual per-platform publisher boundary."""
    long_text = "word " * 1000
    result = build_caption_from_transcript(long_text)
    assert result == " ".join(long_text.split())
    assert len(result) > 2200  # deliberately longer than TikTok's caption cap


def test_validate_caption_mode_accepts_known_modes():
    for mode in ("transcript_auto", "manual", "none"):
        validate_caption_mode(mode)  # must not raise


def test_validate_caption_mode_rejects_unknown_mode():
    with pytest.raises(CaptionConfigError, match="Unsupported CAPTION_MODE='bogus'"):
        validate_caption_mode("bogus")
