"""
caption.py — Deterministic caption candidate derivation from transcript.

What it does:
  Makes caption first-class video/post metadata (config.CAPTION_MODE:
  "transcript_auto" | "manual" | "none") instead of a transient string
  created only at publish time. build_caption_from_transcript() is the
  "transcript_auto" candidate builder: it normalizes whitespace and returns
  the full transcript text unchanged otherwise — no platform-specific
  truncation here. TikTok/Instagram/YouTube length limits belong at the
  eventual per-platform publisher boundary, not baked into the canonical
  stored caption (a video's caption candidate should not conceptually be
  "a TikTok-sized caption"). See
  docs/decisions/0005-fifo-baseline-and-optional-strategy-routing.md.

  "manual" and "none" caption_source assignment is handled inline in
  process_content.py (a simple per-stage skip-if-already-set, matching the
  rest of that pipeline's idempotency pattern) — this module only owns the
  "transcript_auto" derivation logic and mode validation.

Dependencies:
  stdlib only.
"""


class CaptionConfigError(ValueError):
    """Raised for an unsupported config.CAPTION_MODE value."""


VALID_CAPTION_MODES = {"transcript_auto", "manual", "none"}


def validate_caption_mode(mode: str) -> None:
    if mode not in VALID_CAPTION_MODES:
        raise CaptionConfigError(
            f"Unsupported CAPTION_MODE={mode!r}. Valid values: {', '.join(sorted(VALID_CAPTION_MODES))}"
        )


def build_caption_from_transcript(transcript: str | None) -> str | None:
    """Normalize a transcript into a caption candidate: collapse all
    whitespace runs (including newlines) to single spaces and strip the
    ends. Returns None for an empty/whitespace-only/absent transcript
    (nothing to caption from) rather than an empty string."""
    text = " ".join((transcript or "").split())
    return text or None
