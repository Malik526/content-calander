# ADR-0014: Canonical Caption Ownership, Provenance, and the Generation Boundary

## Status

Accepted (Milestone 3.10).

## Context

Milestone 3.10 makes a video's publishing caption user-controllable: visible and editable
from the Queue, persisted, and consumed by the TikTok publisher. Captions already existed as
first-class metadata since Milestone 1.3 (`videos.caption_text`/`caption_source`, ADR-0005),
populated only by the local CLI ingestion pipeline (`media/processing.py`). The long-term
product needs the same video to be publishable to several platforms, each possibly with its
own caption, and Milestone 5 (content intelligence) will make generation smarter. 3.10 has to
set the ownership model without building either of those.

## Decision 1 — The canonical caption stays video-level; per-platform overrides are a later, additive seam

`videos.caption_text` remains the one canonical/default caption. No new table or column is
added. `platform_posts` gets no caption column yet.

Every publisher obtains its caption through `publishing.caption_resolution.resolve_publish_caption(video, platform)`,
which today returns `videos.caption_text`. When a platform first needs different text, a
nullable override goes on that platform's `platform_posts` row and the resolver consults it
first, falling back to the canonical caption. No publisher, API, or UI caller has to change
for that, and captions are never duplicated per platform until a platform actually differs.

Platform length limits stay at each publisher boundary (TikTok's UTF-16 check in
`publishing/tiktok/publisher.py`), never on the canonical caption, as ADR-0005 already required.

**Caption state is keyed by `videos.id`, never `file_hash`.** Since the Milestone 3.7
re-upload change (ADR-0009 addendum), every upload is its own `videos` row, so two records
with byte-identical content are captioned independently with no extra work.

## Decision 2 — Provenance reuses `caption_source`, with an `_edited` suffix instead of version history

`caption_source` keeps its existing values (`transcript_auto`, `manual`, `none`, `NULL`) and
gains one family: `<generator>_edited` (today only `transcript_auto_edited`), written when a
user saves text that differs from generated text. A further edit keeps the same value, with no
suffix stacking. The API collapses this into four user-facing states
(`NONE`/`MANUAL`/`GENERATED`/`GENERATED_EDITED`) and never exposes the raw value.

A user clearing the caption stores `caption_text=NULL, caption_source='manual'`. It stays
non-NULL on purpose: `media/processing.py`'s caption stage only runs when `caption_source IS NULL`,
so the CLI pipeline never re-derives over a user's choice. No schema change, no CHECK-constraint
change (there is none on either backend), and no version history, which the brief explicitly deferred.

## Decision 3 — Regeneration is explicit; captions lock once submitted

- Regenerating over existing text requires `overwrite=true`. Otherwise the backend returns 409
  and writes nothing, and the UI asks for confirmation before sending it, including when only
  an unsaved draft would be lost.
- A caption becomes read-only (409 on write) once any `platform_posts` row for the video is
  `PUBLISHING`/`PUBLISHED` or has ever obtained a `platform_post_id`. At that point the text
  is part of the published record. A `PENDING` row, including one requeued after a
  pre-submission failure, stays editable. `execute_claimed_platform_post` re-reads the video
  at execution time, so the saved caption is what gets published.

## Decision 4 — A generation contract, with only the existing generator behind it

`media/caption_generation.py` defines `CaptionGenerationRequest` (transcript, video metadata,
platform, creator preferences) → `GeneratedCaption` (text, `caption_source` value, metadata)
behind one `generate_caption()` entry point. The only implementation is the existing
deterministic `transcript_auto` derivation, unchanged.

**Known limitation:** hosted uploads are never transcribed (Milestone 3.7's scope; no hosted
worker exists), so generation is unavailable for every hosted upload today. The API reports
`can_generate: false` and the UI says a transcript is needed. Building a hosted transcription
pipeline was explicitly out of scope. Milestone 5 plugs smarter generators, and eventually a
transcript source for hosted media, in behind the same contract.

## Consequences

- No migration on either backend; `ContentStoreProtocol` is unchanged (reads/writes go through
  the existing `get_video`/`update_video`/`list_platform_posts_for_video`).
- The worker-side "missing caption" precondition is unchanged. A scheduled video with no
  caption still fails at publish time exactly as before (`PublishTikTokError`); 3.10 does not
  add a scheduling-time gate.
- Per-platform captions, caption approval states, version history, and smarter generation
  remain future scope (Milestone 5+).

## Addendum — Milestone 3.10.1: derived hashtag metadata

`videos.caption_text` stays the exact publishing truth. Hashtags are derived from it
deterministically (`media/hashtags.py`) and **persisted**, not derived on read. Future
analytics and creator-history work needs a queryable historical dataset, and a read-time
parse would silently change past results whenever the parser changes.

Storage is a relational `video_hashtags` table (`video_id`, `position`, `hashtag`), keeping
the schema's existing "no JSON columns" convention (see the posting-cadence tables). It is not
a column on `videos`. The only write path is `ContentStoreProtocol.set_video_caption`, which
updates the caption and replaces its hashtag rows in one transaction, so derived data cannot
drift from the text. Occurrences are kept in order with duplicates and original casing.
Normalization (case folding, dedup) is left to consumers, because it can be applied later but
not undone. Rows are not backfilled for captions written before 3.10.1. Hashtags are never
sent to a platform separately; TikTok recognizes them inside the caption string.
