"""
hashtags.py — deterministic hashtag extraction from caption text
(Milestone 3.10.1: Caption Hashtag Parsing + Structured Metadata).

What it does:
  extract_hashtags(caption_text) returns the hashtags in a caption, in the
  order they appear, as structured metadata derived from the canonical
  free-form caption. It never modifies, reformats or rebuilds the caption —
  videos.caption_text stays the exact publishing source of truth (TikTok
  recognizes hashtags inside its single caption/title field; there is no
  separate hashtag payload).

  Token rules (plain character scanning, no regex, no LLM):
  - A hashtag is "#" followed by one or more tag characters: any Unicode
    letter or number (str.isalnum — covers "développement", "東京",
    "123challenge"), "_", or a combining mark (Unicode category M*, so
    scripts that rely on combining vowel signs — e.g. Devanagari — and
    decomposed accents are not cut mid-word).
  - The tag ends at the first non-tag character: whitespace, a line
    break, punctuation, emoji, or another "#" ("#a#b" -> "#a", "#b").
  - The "#" must not directly follow a tag character, so mid-word "#"
    ("C#", "abc#def", "page#section") is not a hashtag — except directly
    after another hashtag, so chained tags split ("#coding#saas" ->
    "#coding", "#saas").
  - A bare "#" or "#" followed by a non-tag character is ignored.
  - All-digit tags ("#1", "#2024") are ignored — they are almost always
    numbering ("#1 fan"), not topics. A tag with any non-digit counts.
  - Output keeps the caption's exact spelling with the leading "#" (no
    case folding, no Unicode normalization) and keeps every occurrence,
    duplicates included: there is no project convention for deduplicating,
    and occurrence order/count is information a future analytics consumer
    can collapse but could never recover.

Dependencies:
  stdlib only.
"""

import unicodedata


def _is_tag_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_" or unicodedata.category(ch).startswith("M")


def extract_hashtags(caption_text: str | None) -> list[str]:
    text = caption_text or ""
    hashtags: list[str] = []
    previous_tag_end = -1
    i = 0
    while i < len(text):
        if text[i] != "#" or (i > 0 and _is_tag_char(text[i - 1]) and i != previous_tag_end):
            i += 1
            continue
        end = i + 1
        while end < len(text) and _is_tag_char(text[end]):
            end += 1
        body = text[i + 1:end]
        if body and not body.isdigit():
            hashtags.append(f"#{body}")
            previous_tag_end = end
        i = end if end > i + 1 else i + 1
    return hashtags
