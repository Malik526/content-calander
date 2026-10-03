"""
caption_resolution.py — which caption a platform post publishes with
(Milestone 3.10: Caption Generation + Editing).

What it does:
  resolve_publish_caption(video, platform) is the single source-of-truth
  lookup every publisher path uses to obtain the caption it sends. Today
  every platform publishes the canonical, user-editable
  videos.caption_text — there is no per-platform caption column yet.

  This is the seam for per-platform captions later: when a platform needs
  its own text (TikTok vs. Instagram vs. YouTube), add a nullable override
  on that platform's platform_posts row and consult it here first, falling
  back to the canonical caption. No caller has to change. Platform length
  limits stay at each publisher boundary (e.g. publishing/tiktok/
  publisher.py's UTF-16 check), never baked into the canonical caption.

  Captions are optional (Milestone 3.14 follow-up): None means "publish
  without a caption", and every publisher must accept it. A blank or
  whitespace-only stored value is the same as no caption — never a
  publishing failure, and never replaced with a placeholder. A real
  caption is returned exactly as stored. (Platform-specific or automatic
  captions are the future intelligence layer's concern — they would fill
  this optional value, not make it mandatory.)

Dependencies:
  persistence.content_store.VideoRecord.
"""

from content_automation.persistence.content_store import VideoRecord


def resolve_publish_caption(video: VideoRecord, platform: str) -> str | None:
    # `platform` is unused until per-platform overrides exist; it is part of
    # the contract so callers already say which platform they publish to.
    text = video.caption_text
    return text if text is not None and text.strip() else None
