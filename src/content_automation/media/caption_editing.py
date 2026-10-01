"""
caption_editing.py — saving, regenerating and describing a video's
canonical caption (Milestone 3.10: Caption Generation + Editing).

What it does:
  The one place that writes videos.caption_text/caption_source on behalf of
  a user (the captions API calls this; nothing else in the hosted path
  writes them). Three rules live here:

  Provenance — caption_source keeps its existing meaning and values
  ("transcript_auto", "manual", "none", or NULL) plus one new family: a
  generator's source with an "_edited" suffix ("transcript_auto_edited")
  when a user changes generated text. So a manual edit after generation
  never loses the fact that generation contributed to it, without a
  version-history table. caption_provenance() collapses all of this into
  the four user-facing states the API reports: NONE, MANUAL, GENERATED,
  GENERATED_EDITED.

  Explicit regeneration — regenerate_caption() refuses to replace existing
  caption text unless the caller passes overwrite=True
  (CaptionOverwriteRequiredError otherwise), so a manually edited caption
  is never silently destroyed.

  Locking — once any platform_posts row for the video has started or
  finished a real submission (PUBLISHING/PUBLISHED, or a platform_post_id
  was ever obtained), the caption is part of the published record and is
  no longer editable (CaptionLockedError). A PENDING post — including one
  requeued after a pre-submission failure — stays editable, and the worker
  reads the caption fresh at execution time, so the saved value is what
  gets published.

  Hashtags (Milestone 3.10.1) — every write here goes through
  ContentStoreProtocol.set_video_caption with
  media.hashtags.extract_hashtags(text), so the derived video_hashtags
  rows are replaced atomically with caption_text on every save, generate,
  regenerate and clear. caption_text itself is stored exactly as entered
  (apart from trimming surrounding whitespace, unchanged from 3.10).

  Caption state is keyed by videos.id only, never file_hash: two records
  with byte-identical content are captioned independently (see ADR-0009's
  re-upload addendum for why file_hash is not an identity).

Dependencies:
  persistence.protocol.ContentStoreProtocol, media.caption_generation,
  media.hashtags.
"""

from content_automation.media.caption_generation import (
    TRANSCRIPT_AUTO_SOURCE,
    build_generation_request,
    generate_caption,
)
from content_automation.media.hashtags import extract_hashtags
from content_automation.persistence.content_store import VideoRecord
from content_automation.persistence.protocol import ContentStoreProtocol

GENERATED_SOURCES = frozenset({TRANSCRIPT_AUTO_SOURCE})
EDITED_SUFFIX = "_edited"
MANUAL_SOURCE = "manual"

# platform_posts statuses at which the caption has been handed to a platform.
_LOCKED_POST_STATUSES = frozenset({"PUBLISHING", "PUBLISHED"})


class CaptionLockedError(Exception):
    """The caption has already been submitted to a platform."""


class CaptionOverwriteRequiredError(Exception):
    """Regeneration would replace existing caption text without overwrite=True."""


def _generated_base(source: str | None) -> str | None:
    """The generator source behind `source` ("transcript_auto" for both
    "transcript_auto" and "transcript_auto_edited"), or None if the caption
    was not generated."""
    if source is None:
        return None
    base = source[: -len(EDITED_SUFFIX)] if source.endswith(EDITED_SUFFIX) else source
    return base if base in GENERATED_SOURCES else None


def caption_provenance(video: VideoRecord) -> str:
    if not video.caption_text:
        return "NONE"
    base = _generated_base(video.caption_source)
    if base is None:
        return "MANUAL"
    return "GENERATED_EDITED" if video.caption_source.endswith(EDITED_SUFFIX) else "GENERATED"


def normalize_caption_text(text: str | None) -> str | None:
    """Trim surrounding whitespace only — internal line breaks, spacing and
    hashtags are the user's formatting. Empty means "no caption" (NULL)."""
    stripped = (text or "").strip()
    return stripped or None


def source_after_manual_edit(previous_source: str | None, previous_text: str | None, new_text: str | None) -> str | None:
    """caption_source after a user saves new_text over previous_text."""
    if new_text == previous_text:
        return previous_source
    if new_text is None:
        # Cleared. Still non-NULL so media.processing's caption stage stays
        # idempotent and never re-derives over the user's choice.
        return MANUAL_SOURCE
    base = _generated_base(previous_source)
    return f"{base}{EDITED_SUFFIX}" if base is not None else MANUAL_SOURCE


def is_caption_locked(store: ContentStoreProtocol, video_id: int) -> bool:
    return any(
        post.status in _LOCKED_POST_STATUSES or post.platform_post_id is not None
        for post in store.list_platform_posts_for_video(video_id)
    )


def save_caption(store: ContentStoreProtocol, video: VideoRecord, text: str | None) -> VideoRecord:
    if is_caption_locked(store, video.id):
        raise CaptionLockedError("This video has already been submitted for publishing; its caption can no longer change.")
    new_text = normalize_caption_text(text)
    new_source = source_after_manual_edit(video.caption_source, video.caption_text, new_text)
    if new_text != video.caption_text or new_source != video.caption_source:
        store.set_video_caption(video.id, new_text, new_source, extract_hashtags(new_text))
    return store.get_video(video.id)


def regenerate_caption(
    store: ContentStoreProtocol, video: VideoRecord, *, overwrite: bool, platform: str | None = None,
) -> VideoRecord:
    """Generate and persist a caption. Raises CaptionLockedError,
    CaptionOverwriteRequiredError, or
    caption_generation.CaptionGenerationUnavailableError; nothing is written
    in any of those cases."""
    if is_caption_locked(store, video.id):
        raise CaptionLockedError("This video has already been submitted for publishing; its caption can no longer change.")
    if video.caption_text and not overwrite:
        raise CaptionOverwriteRequiredError(
            "This video already has a caption. Confirm replacing it to generate a new one."
        )
    generated = generate_caption(build_generation_request(video, platform))
    store.set_video_caption(video.id, generated.text, generated.source, extract_hashtags(generated.text))
    return store.get_video(video.id)
