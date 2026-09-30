"""
caption_generation.py — the caption-generation contract (Milestone 3.10:
Caption Generation + Editing).

What it does:
  Defines the one interface every caption generator sits behind:
  CaptionGenerationRequest (transcript, video metadata, target platform,
  creator preferences) in, GeneratedCaption (text + the caption_source
  value to persist + generation metadata) out. generate_caption() is the
  single entry point callers use; today it has exactly one real
  implementation — the existing deterministic "transcript_auto" derivation
  (media.caption.build_caption_from_transcript), unchanged.

  Deliberately a contract, not an intelligence system: platform,
  video_metadata and creator_preferences are accepted but not yet consulted
  by the transcript_auto generator. Milestone 5 (content intelligence) is
  expected to add smarter generators behind this same function without any
  caller — the captions API, a future worker — changing shape.

  Generation needs a transcript. Only the local CLI ingestion pipeline
  (media.processing) transcribes today; hosted uploads (Milestone 3.7) are
  never transcribed, so for them generation is honestly unavailable
  (CaptionGenerationUnavailableError) rather than faked. can_generate_caption()
  lets the API report that up front without exposing the transcript itself.

Dependencies:
  content_automation.media.caption, persistence.content_store.VideoRecord.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from content_automation.media.caption import build_caption_from_transcript
from content_automation.persistence.content_store import VideoRecord

TRANSCRIPT_AUTO_SOURCE = "transcript_auto"


class CaptionGenerationUnavailableError(Exception):
    """No generator can produce a caption from this request (today: the
    video has no usable transcript)."""


@dataclass(frozen=True)
class CaptionGenerationRequest:
    transcript: str | None
    platform: str | None = None
    video_metadata: Mapping[str, Any] = field(default_factory=dict)
    creator_preferences: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedCaption:
    text: str
    # The videos.caption_source value to persist for this text.
    source: str
    # Generator name/version etc. Returned to in-process callers only — not
    # persisted or exposed by the API in 3.10 (no version history yet).
    metadata: Mapping[str, Any] = field(default_factory=dict)


def build_generation_request(video: VideoRecord, platform: str | None = None) -> CaptionGenerationRequest:
    """Assemble a generation request from a stored video record."""
    return CaptionGenerationRequest(
        transcript=video.transcript,
        platform=platform,
        video_metadata={
            "original_filename": video.original_filename,
            "duration_seconds": video.duration_seconds,
            "transcript_language": video.transcript_language,
            "classified_pillar": video.classified_pillar,
        },
    )


def generate_caption(request: CaptionGenerationRequest) -> GeneratedCaption:
    text = build_caption_from_transcript(request.transcript)
    if text is None:
        raise CaptionGenerationUnavailableError(
            "No transcript is available for this video, so a caption can't be generated yet. "
            "Write one manually instead."
        )
    return GeneratedCaption(text=text, source=TRANSCRIPT_AUTO_SOURCE, metadata={"generator": TRANSCRIPT_AUTO_SOURCE})


def can_generate_caption(video: VideoRecord) -> bool:
    return build_caption_from_transcript(video.transcript) is not None
