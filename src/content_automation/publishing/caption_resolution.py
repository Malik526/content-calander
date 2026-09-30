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

Dependencies:
  persistence.content_store.VideoRecord.
"""

from content_automation.persistence.content_store import VideoRecord


def resolve_publish_caption(video: VideoRecord, platform: str) -> str | None:
    # `platform` is unused until per-platform overrides exist; it is part of
    # the contract so callers already say which platform they publish to.
    return video.caption_text
