# Milestone 2.1.1 — Due-Post Detection

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

**Correction (Milestone 2.1.2, same day)**: this milestone's original selection contract included `PUBLISHING` as an eligible/due status. That was corrected in 2.1.2 to `PENDING`-only — `PUBLISHING` means already claimed/in progress, not available for initial execution; see `docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md` for the reasoning. The "Selection Contract" and "Eligible / Excluded States" sections below are left as originally recorded (not rewritten) with this note instead, per this repository's changelog/documentation policy of not rewriting historical entries — treat `due_post_selector.ELIGIBLE_STATUSES` in the actual code, not this doc, as current truth for what's eligible. The architectural gap this milestone reported (below) **was** fixed in 2.1.2 — `platform_post_materializer.py` now creates the `PENDING` row this doc predicted was needed.

## Purpose

Answer exactly one question, trustworthily and deterministically:

> Given the database and a supplied current time, which TikTok posts are due right now?

Read/selection logic only. Explicitly out of scope for this milestone: worker loops, cron/scheduler integration, atomic claiming, retries, crash recovery, actual publishing side effects, and missed-schedule policy beyond identifying that a post is overdue/due. Ownership and execution belong to 2.1.2+.

## Selection Contract

```
a post is due when:
  platform == <given platform>
  AND scheduled_at IS NOT NULL
  AND scheduled_at <= now   (inclusive)
  AND status IN ("PENDING", "PUBLISHING")
```

Implemented as `due_post_selector.get_due_posts(store, platform, now=None)`, backed by a new pure-SQL `ContentStore.get_due_platform_posts(platform, now_iso, eligible_statuses)` method. Returns `list[PlatformPostRecord]` — the existing dataclass (`id`, `video_id`, `platform`, `status`, `platform_post_id`, `scheduled_at`, `published_at`, `failure_reason`, `created_at`, `updated_at`) already carries everything a future worker milestone needs to identify and execute the job; no new DTO was introduced. No network calls, no raw video bytes loaded.

## Time Semantics

- `platform_posts.scheduled_at` (like `content_slots.scheduled_at`, from which it is copied at submission time) is stored as a **naive local-time ISO string in `config.TIMEZONE`** (`America/New_York` by default) — *not* UTC, *not* timezone-aware. Confirmed directly against real rows, e.g. `"2026-09-16T09:00:00"`, in contrast to `created_at`/`updated_at`, which *are* timezone-aware UTC (`"...+00:00"`) — two different datetime representations coexist in the same table for different columns.
- This is an existing, established convention — `slot_matcher.now_in_config_timezone()` already exists for exactly this reason ("content_slots.scheduled_at is stored as a naive local datetime isoformat... so 'now' must be computed in the same timezone before the naive-string comparison"). `due_post_selector.py` imports and reuses that function rather than introducing a second time model.
- Comparison is a plain string comparison (`scheduled_at <= ?` in SQL, bound to `now.isoformat()`), matching the same pattern `find_earliest_open_slot_fifo` already uses. Verified this is correct regardless of microsecond presence/absence (ISO 8601 fixed-width fields compare lexicographically the same as chronologically; a shorter (no-microsecond) prefix always sorts before a longer continuation, matching real chronological order).
- Boundary is inclusive by design and by test: a post scheduled exactly at `now` is due.
- `now` defaults to `now_in_config_timezone()` (current wall-clock, naive) but accepts an injected fixed `datetime` — every test uses a fixed `NOW`, no wall-clock sleeps.

## Eligible / Excluded States

The real state machine (confirmed by grepping every `platform_posts.status` assignment in the codebase, not assumed) has exactly four values: `PENDING`, `PUBLISHING`, `PUBLISHED`, `FAILED`.

- **Eligible:** `PENDING` (never submitted) and `PUBLISHING` (submitted, not yet resolved) — both still "in play."
- **Excluded:** `PUBLISHED` (completed) and `FAILED`. `FAILED` is excluded on the strength of `publish_tiktok.py`'s own module docstring, which explicitly calls a `FAILED` record "terminal... rather than silently retried."
- **Noted, not fixed:** `publish_tiktok.py`'s own manual-rerun path does not itself re-check `status` before re-polling an already-`FAILED` row that has a `platform_post_id` set — it only special-cases `status == "PUBLISHED"` for the early-return. This selector is intentionally stricter/more correct about terminal-ness than that existing quirk. Not changed here — redesigning `platform_posts`/`publish_tiktok.py` behavior is out of this milestone's scope.
- Also excluded per the contract: rows with `scheduled_at IS NULL`, rows for a different `platform`, and future-scheduled rows.

## Ordering

`ORDER BY scheduled_at ASC, id ASC` — earliest-due first, with row `id` as a stable tiebreaker for equal `scheduled_at` values (deterministic output every time, never dependent on SQLite's unspecified tie order).

## Tests

`tests/test_due_post_selector.py`, 12 focused tests, fixed injected `NOW`, no wall-clock sleeps:

1. past `PENDING` post is returned
2. post scheduled exactly at `now` is returned (inclusive boundary)
3. future post is excluded
4. `PUBLISHED` post is excluded
5. `FAILED` post is excluded
6. unrelated platform is excluded
7. `NULL scheduled_at` is excluded
8. multiple due posts return earliest-first
9. stable ordering when `scheduled_at` values are equal (tiebreak by id)
10. naive-local-time boundary behavior (asserts `now.tzinfo is None`, guarding against a regression to aware datetimes that would `TypeError` or silently miscompare)
11. the default-`now` path (`now_in_config_timezone()`) resolves without error
12. the selector has no mutation/side effects (row is byte-identical before/after)

Result: **12 passed**. Full suite: **439 passed** (427 pre-existing + 12 new), no regressions.

## Real DB Validation

Against the live `data/content.db` (2026-09-17):

| `video_id` | `status` | `platform_post_id` | `scheduled_at` |
|---|---|---|---|
| 1 | `FAILED` | *(none)* | 2026-09-16T09:00:00 (past) |
| 2 | `PUBLISHED` | `v_pub_file~v2-1.7686313615539865614` | 2026-09-18T09:00:00 |

`due_post_selector.get_due_posts(store, "tiktok")` against this real data returns **`[]`** — correct: video 1 is `FAILED` (terminal, excluded), video 2 is `PUBLISHED` (excluded). No live TikTok post was made for this milestone, per instruction — there was nothing naturally due to act on anyway.

**A concrete, present-tense confirmation of the architectural gap below**: videos 3, 4, and 5 are all `ASSIGNED` to real `content_slots` (scheduled 2026-09-19, 09-21, 09-23) but have **no `platform_posts` row at all** — `publish_tiktok.py` has never been invoked for them. The selector is functionally correct against what exists in `platform_posts`, but cannot surface these three videos even after their slot times pass, because nothing pre-creates a `PENDING` row for them ahead of manual execution.

## Architectural Gap — Reported, Not Fixed

`publish_tiktok.py` only creates a `platform_posts` row at the moment a human runs it for a specific `--video-id`. Nothing in the current pipeline creates a `PENDING` `platform_posts` row when a video is assigned a `content_slot` (i.e., at `ContentStore.assign_slot()` time, in `process_content.py`/`slot_matcher.py`'s flow) or on any other schedule. This makes `get_due_posts()` correct-but-inert for any video that hasn't already had a manual publish attempt: `content_slots.scheduled_at` can be arbitrarily far in the past with no corresponding due `platform_posts` row to find.

This is reported, per instruction, rather than silently redesigned. **Proposed smallest compatible solution** for a future milestone: when a video's `content_slot` is assigned (`ContentStore.assign_slot`), also call `insert_platform_post(video_id, platform, created_at=now, scheduled_at=slot.scheduled_at)` for each platform the video should eventually be published to — populating `platform_posts` with a `PENDING` row immediately, well ahead of its actual due time, so this exact selector already works correctly with no further schema or query changes. This was **not implemented** here: it touches `process_content.py`/`slot_matcher.py`'s assignment flow, which is explicitly out of this milestone's scope ("refactor unrelated modules" is a stated guardrail), and it's a mutation, not selection.

## Conclusion

`due_post_selector.get_due_posts()` gives a trustworthy, deterministic answer to "which TikTok posts are due right now," with correct inclusive-boundary and naive-local-time handling matching this repository's existing time model, correct exclusion of every terminal/future/unrelated-platform/unscheduled case, and zero side effects. No ADR was warranted — this is a straightforward selection query following the same shape and conventions `slot_matcher.py` already established; no new architectural decision was made. One real architectural gap was found and reported rather than fixed: `platform_posts` rows are not currently pre-created early enough for this selector to see naturally-due videos that have never been manually published at least once — full detail and a proposed fix above, left for a future milestone to decide on.

**Milestone 2.1.1: COMPLETE.**
