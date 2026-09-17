# Milestone 2.1.2 — Scheduled Platform-Post Materialization + Pending-Only Due State

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

## Purpose

Close the architectural gap Milestone 2.1.1 found and reported: a video assigned to a `content_slot` left no trace in `platform_posts`, so `due_post_selector.get_due_posts()` could never discover a scheduled delivery until a human manually ran `publish_tiktok.py` for that video at least once. Also correct 2.1.1's due-post selector so it aligns with the intended execution lifecycle — `PENDING` (eligible for initial execution) is a materially different state from `PUBLISHING` (already claimed/in progress), and conflating the two in ordinary due detection would make a future atomic-claim milestone's `PENDING -> PUBLISHING` transition meaningless.

## Gap Being Closed

From the real database, confirmed in the 2.1.1 evaluation and reconfirmed here before any change: videos 3, 4, and 5 were `ASSIGNED` to real `content_slots` (scheduled 2026-09-19, 09-21, 09-23) with **no `platform_posts` row at all** — nothing would ever surface them to a future worker, no matter how overdue they became.

## Chosen Materialization Boundary

**Not** folded into `ContentStore.assign_slot()`. That primitive's job is narrowly "atomically claim a slot for a video" — it has existing callers and tests (`tests/test_slot_matcher.py`) with no platform notion at all, and widening its contract to also decide platform-delivery intent would widen it for every caller, including ones that shouldn't need to care. Instead, `platform_post_materializer.materialize_platform_posts_for_assignment(store, video_id, slot_id, created_at)` is called as a clearly separate, additive step immediately after `assign_slot()` succeeds, in `process_content.py`. This is not perfectly atomic with the slot claim itself — a crash between the two calls would leave the pre-2.1.2 gap for that one video — but crash recovery is explicitly out of this milestone's scope, and a real backfill script (below) exists for exactly this kind of gap regardless of cause.

Platform selection is **not** hard-coded into the materializer: `config.TARGET_PUBLISHING_PLATFORMS` (default `["tiktok"]`, overridable via `CONTENT_CALENDAR_TARGET_PUBLISHING_PLATFORMS`) is a plain list the materializer iterates. `publisher.build_publisher()` still only has a real `TikTokPublisher` implementation — adding a platform to this list alone does nothing until a real `Publisher` exists for it, but the materializer itself needs no code change when that day comes: `video → slot → N platform_posts rows` (one per configured platform) is already the shape.

## Lifecycle Semantics

```
ASSIGNED video
   ↓
PENDING platform_post        (this milestone: materialized at assignment time)
   ↓  [next milestone: atomic claim]
PUBLISHING
   ↓
PUBLISHED / FAILED
```

- **PENDING** — never yet submitted; eligible for initial execution (returned by `due_post_selector.get_due_posts()`).
- **PUBLISHING** — submitted, not yet resolved; already claimed/in progress. **Not** returned by ordinary due detection as of this milestone (was returned in 2.1.1 — corrected here).
- **PUBLISHED** — terminal success. Not returned.
- **FAILED** — terminal failure for now. Not returned (unchanged from 2.1.1's reasoning).

## Idempotency Behavior

`ContentStore.insert_platform_post_if_missing(video_id, platform, scheduled_at, created_at)` is `INSERT OR IGNORE` against the existing `UNIQUE(video_id, platform)` constraint — mirrors `insert_slot_if_missing`'s exact pattern rather than introducing a new one. It **never runs an UPDATE**, so a repeated call for an already-materialized `(video, platform)` pair is a true no-op: it cannot reset `status`, `platform_post_id`, `published_at`, or `failure_reason` on an existing row, regardless of that row's current state (`PENDING`, `PUBLISHING`, `PUBLISHED`, or `FAILED`) — verified directly by a parametrized test exercising all three non-`PENDING` states (below).

## PENDING-Only Due Selection Correction

`due_post_selector.ELIGIBLE_STATUSES` changed from `["PENDING", "PUBLISHING"]` to `["PENDING"]`. `due_post_selector.py`'s module docstring and the 2.1.1 doc (via a correction note, not a silent rewrite) both now explain why: stale/interrupted `PUBLISHING` recovery belongs to a later crash-recovery milestone and must not be conflated with ordinary due detection, which should only ever surface unclaimed work.

## Manual Publish Compatibility (Phase 6)

Traced `publish_tiktok.py`'s `publish_video()` against a pre-materialized `PENDING` row (not just reasoned about — exercised with a real test, `test_publish_video_uses_pre_materialized_pending_row`): it looks up the existing record via `get_platform_post()` first, and since `record is not None`, it does **not** call `insert_platform_post()` again — it reuses the existing row directly, avoiding the `UNIQUE(video_id, platform)` `IntegrityError` a naive re-insert would cause. **No code change to `publish_tiktok.py` was needed** — its existing "check for an existing row before inserting" logic already correctly handles both the new common case (a pre-materialized `PENDING` row) and the legacy case (no row at all, e.g. a video published without ever going through slot assignment), which the pre-existing `test_publish_video_full_happy_path`/`test_publish_video_with_no_assigned_slot_leaves_scheduled_at_none` tests continue to cover unchanged.

## Tests

`tests/test_platform_post_materializer.py` (8 tests): materializes with correct `video_id`/`platform`, new row is `PENDING` with `platform_post_id IS NULL`, `scheduled_at` exactly equals the assigned slot's, repeated materialization creates no duplicate, a parametrized test (`PUBLISHED`/`PUBLISHING`/`FAILED`) confirms repeated materialization never resets an existing row's state, and an unknown `slot_id` raises clearly.

`tests/test_due_post_selector.py`: added `test_publishing_post_is_excluded`; fixed `test_multiple_due_posts_return_earliest_first` (previously asserted a `PUBLISHING` row was included — now all three fixture rows are `PENDING`, since ordering and eligibility are now two independent, non-conflated concerns).

`tests/test_publish_tiktok.py`: added `test_publish_video_uses_pre_materialized_pending_row` (Phase 6 proof).

`tests/test_backfill_platform_posts.py` (6 tests): backfills a missing row correctly, leaves an existing row (of any status) completely untouched, ignores an unassigned video, `--dry-run` reports without writing, a second run after backfill creates nothing (idempotent), and multiple assigned videos are all correctly backfilled in one pass.

Full suite, including the pre-existing `tests/test_slot_matcher.py` (`assign_slot()` itself, unchanged) and `tests/test_process_content_integration.py`, was run — not skipped.

Result: 16 new tests added this milestone (1 in `test_due_post_selector.py` + 8 in the new `test_platform_post_materializer.py` + 1 in `test_publish_tiktok.py` + 6 in the new `test_backfill_platform_posts.py`), all passing. Full suite: **455 passed** (439 at the end of Milestone 2.1.1 + 16 new), no regressions.

## Real DB Validation / Backfill

Before this milestone's code changes, confirmed via direct query: videos 3, 4, 5 were `ASSIGNED` with no `platform_posts` row; videos 1 (`FAILED`) and 2 (`PUBLISHED`) already had one.

Decision: **B — backfill**, per the brief's own preference ("so the real current schedule becomes usable for later worker milestones") — these are real, already-scheduled videos that would otherwise remain permanently un-discoverable, since materialization at assignment time only takes effect for *new* assignments going forward.

`python3 backfill_platform_posts.py --dry-run` first (reported exactly the 3 expected rows, wrote nothing), then `python3 backfill_platform_posts.py` for real, after taking a plain file-copy backup of `data/content.db` first as a precaution (first real run of a new DB-mutating script against production data).

**Before:**

| `video_id` | `platform_posts` row |
|---|---|
| 1 | `FAILED` (pre-existing, untouched) |
| 2 | `PUBLISHED` (pre-existing, untouched) |
| 3, 4, 5 | *(none)* |

**After:**

| `video_id` | `status` | `scheduled_at` |
|---|---|---|
| 1 | `FAILED` | 2026-09-16T09:00:00 *(unchanged — verified `platform_post_id`/`published_at`/`failure_reason` byte-identical to before)* |
| 2 | `PUBLISHED` | 2026-09-18T09:00:00 *(unchanged, same verification)* |
| 3 | `PENDING` | 2026-09-19T09:00:00 |
| 4 | `PENDING` | 2026-09-21T09:00:00 |
| 5 | `PENDING` | 2026-09-23T09:00:00 |

`due_post_selector.get_due_posts(store, "tiktok")` against the real DB immediately after backfill: **`[]`** — correct. Today is 2026-09-17; all three newly-materialized rows are legitimately future-scheduled (09-19 onward), so none are actually due yet. This is exactly the intended outcome: the gap is closed (the rows now exist and will correctly surface once their times arrive), not a live TikTok post forced into existence. Nothing was published to TikTok during this milestone.

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md`. The 2.1.1 record was corrected in place with a dated note (not silently rewritten) pointing here.

## Conclusion

Scheduled content now reliably gets a `PENDING` `platform_posts` row the moment it's assigned a `content_slot`, materialized additively and idempotently alongside the existing `assign_slot()` call rather than inside it, with zero risk of resetting real publishing progress on repeat calls. The due-post selector now correctly distinguishes "eligible for initial execution" from "already claimed," setting up a future atomic-claim milestone's `PENDING -> PUBLISHING` transition to mean something real. The pre-2.1.2 gap in the live database was backfilled once, safely and idempotently, using the same primitive real assignment now uses going forward. No ADR was warranted: this is a lifecycle clarification and a materialization step built entirely from this repository's existing conventions (`insert_slot_if_missing`'s exact idempotency pattern, `slot_matcher.py`'s exact module shape, `migrate_relocated_paths.py`'s exact one-time-script shape) — no new architectural decision was made.

**Milestone 2.1.2: COMPLETE.**
