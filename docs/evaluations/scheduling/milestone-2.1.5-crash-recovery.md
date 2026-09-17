# Milestone 2.1.5 — Crash Recovery for Interrupted Publishing Jobs

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

## Purpose

Answer: what happens if Pickle Batch dies halfway through posting? After this milestone: it notices the abandoned job, figures out how far it got, and continues safely without posting the video twice.

## Crash Classes

Traced every point the process can stop after `PENDING -> PUBLISHING` (via `execute_claimed_platform_post`, Milestone 2.1.4) — confirmed exactly two meaningful classes, distinguishable with the existing schema, no new columns needed:

- **Case A — claimed but never submitted**: `status = PUBLISHING`, `platform_post_id IS NULL`. No evidence TikTok ever accepted anything.
- **Case B — submitted but unresolved**: `status = PUBLISHING`, `platform_post_id IS NOT NULL`. TikTok already accepted the submission; the media must never be resubmitted.

No other meaningful persisted state exists — the full `platform_posts.status` vocabulary remains exactly `PENDING`/`PUBLISHING`/`PUBLISHED`/`FAILED` (unchanged since Milestone 2.1.1's codebase-wide grep).

## Stale Definition

`config.PLATFORM_POST_STALE_MINUTES` (default 30, env-overridable via `CONTENT_CALENDAR_PLATFORM_POST_STALE_MINUTES`) — centrally defined, not a magic number scattered in code. Compared against `platform_posts.updated_at`, confirmed sufficient with no new lease/heartbeat column: every write that mutates a `platform_posts` row already bumps `updated_at` (claim, submission, poll outcomes), so it's already a reliable "last known activity" signal.

Confirmed (not assumed) that `updated_at` is *always* written as an **aware UTC** isoformat string (`publish_tiktok._now_iso`/`worker._now_iso`, both `datetime.now(timezone.utc).isoformat()`) — a genuinely different convention from `scheduled_at`'s naive-local-time convention (`due_post_selector.py`/`slot_matcher.py`). Mixing the two would have silently miscompared. `crash_recovery.py` uses aware UTC throughout; `ContentStore.get_recoverable_platform_posts`'s docstring calls this out explicitly to prevent a future regression.

Default (30 min) is comfortably longer than the longest legitimate single execution attempt — `tiktok_publisher.py`'s upload timeout alone is 300s (5 min) — so an actively-running worker is never mistaken for a crashed one.

## Recovery Selection Contract

`ContentStore.get_recoverable_platform_posts(platform, stale_before_iso) -> list[PlatformPostRecord]`: `status = 'PUBLISHING' AND updated_at < stale_before_iso`, ordered oldest-updated first (ties by `id`). Kept deliberately separate from `get_due_platform_posts` (`PENDING`-only, unchanged) — recovery and ordinary due detection are two different concerns reading two different slices of the same table.

## Case A — No `platform_post_id`

Requeued to `PENDING` via the new `ContentStore.update_platform_post_if_unchanged()` (see Atomic Recovery Protections) — never republished directly inside recovery. This puts it back through the one real ownership mechanism, `claim_platform_post()` (2.1.3/2.1.4), so a future worker pass claims it exactly like any other due work. Verified: `publisher.publish()`/`get_status()` are never called during Case A recovery.

## Case B — `platform_post_id` Present

Never resubmitted — Milestone 2.0's idempotency rule holds unconditionally, verified directly by test (`publisher.publish()` call count is `0` in every Case B test, including both local crash simulations). Only `publisher.get_status()` (read-only) is called, exactly mirroring an ordinary poll: `PUBLISH_COMPLETE -> PUBLISHED`, `FAILED -> FAILED` (with `failure_reason`), anything else leaves the row `PUBLISHING` untouched.

`updated_at` is deliberately **not** refreshed for the "still processing" outcome — refreshing it would reset the staleness clock and could delay noticing an already-complete job by a full threshold period; leaving it untouched means the next recovery pass rechecks immediately, which is safe since `get_status()` is read-only and idempotent.

## Duplicate Safety

Proof, not assumption: every Case A and Case B test asserts `len(publisher.publish_calls) == 0` after recovery runs, including both local crash simulations (Scenario A: publisher never called at all since nothing was ever submitted; Scenario B: `publish_calls` explicitly asserted empty even though a submission already exists and gets polled).

## Atomic Recovery Protections

`ContentStore.update_platform_post_if_unchanged(post_id, expected_updated_at, updated_at, **fields)` — a general optimistic-concurrency primitive: `UPDATE ... WHERE id = ? AND updated_at = ?`, success read from `rowcount`, exactly mirroring `claim_platform_post`'s atomic-conditional-update pattern but generalized to arbitrary field writes gated on the row still having the exact `updated_at` the caller last read. Used for *every* recovery write (both Case A's requeue and Case B's poll-outcome writes) — the invariant "recovery may act only on a row that is still the stale record it inspected" is enforced uniformly, not just for the case the brief called out explicitly.

Verified directly, not just reasoned about: `test_recovery_does_not_overwrite_a_row_that_changed_after_selection` selects a stale row, then — simulating the original worker actually finishing the job between selection and recovery's write — mutates it to `PUBLISHED` with a real `platform_post_id` via a separate `update_platform_post` call, then runs recovery. Recovery's own re-query correctly finds nothing stale anymore (`get_status()` never even called), and the row's `PUBLISHED`/`platform_post_id` from the "original worker" is completely untouched.

## Tests

`tests/test_content_store.py` (+8): recent `PUBLISHING` not recoverable; stale `PUBLISHING` selected; `PENDING`/`PUBLISHED`/`FAILED` never recoverable (parametrized); oldest-updated-first ordering; `update_platform_post_if_unchanged` succeeds when `updated_at` matches and fails cleanly (no exception, no partial write) when it doesn't.

`tests/test_crash_recovery.py` (new, 18 tests): staleness/discovery (3), Case A behavior (3: requeues, can be reclaimed, never touches the publisher), Case B behavior (5: never resubmits, polls exactly once, and the three poll outcomes), atomic safety (3: the race-simulation test above, no duplicate rows created, never calls `publish()` when a `platform_post_id` exists), and both Phase 8 local crash simulations (2, using real `ContentStore` temp-file fixtures rather than isolated units — Scenario A and Scenario B, both matching the brief's exact sequence).

Result: **26 new tests**, all passing. Full suite: **503 passed** (477 prior + 26 new), no regressions.

## Local Crash Simulations

Ran outside pytest too, via the real `crash_recovery.recover_stale_posts_once()` entry point against a temp SQLite DB:

**Scenario A** (claim, crash before `publish()`, age past threshold, recover): before = `PUBLISHING`; `summary = RecoverySummary(discovered=1, requeued=1, ...)`; after = `PENDING`, `platform_post_id=None`; reclaim succeeds (`True`), row becomes `PUBLISHING` again.

**Scenario B** (claim, persist `platform_post_id`, crash before poll, age past threshold, recover with a completing `FakePublisher`): before = `PUBLISHING`; `summary = RecoverySummary(discovered=1, polled=1, published=1, ...)`; `publisher.publish_calls` length **0**; after = `PUBLISHED`, `platform_post_id` preserved (`pub_scenario_b`).

Both match the brief's expected outcomes exactly.

## Real DB

Confirmed via direct query and via the real (read-only) `get_recoverable_platform_posts` call against production `data/content.db`: **zero** `PUBLISHING` rows exist at all right now (videos 1/2 are `FAILED`/`PUBLISHED`, videos 3/4/5 are `PENDING`), so zero stale/recoverable rows — nothing for recovery to have found or touched even if it had been run for real. `recover_stale_posts_once()` was never invoked against the real store; only the underlying read-only selector was, to confirm this. Every `updated_at` timestamp for videos 1–5 is byte-identical to prior milestones' checks. **No TikTok API calls were made** — every test and local simulation used `FakePublisher`.

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.5-crash-recovery.md`. `docs/evaluations/scheduling/milestone-2.1.4-worker-execution.md`'s explicitly-deferred stuck-`PUBLISHING` condition is now handled — noted there with a dated completion note (see that file).

## Conclusion

A worker that dies mid-job is now recoverable without ever risking a duplicate TikTok submission: a claimed-but-unsubmitted job is safely returned to the normal claim pool, and a submitted-but-unresolved job is safely resumed by polling the same TikTok operation, never re-uploading. Every recovery mutation is gated by the same kind of atomic, optimistic-concurrency check `claim_platform_post()` established in 2.1.3, applied uniformly rather than only where the brief explicitly called it out, and verified against a genuine after-selection race, not just reasoned about. No ADR was warranted — this is a narrow read/recover primitive built entirely from already-established patterns (conditional `UPDATE ... WHERE`, the `updated_at`-as-CAS-token convention, the one-pass-function-plus-summary shape from `worker.py`), not a new architectural decision.

**Milestone 2.1.5: COMPLETE.**

---

**2026-09-17 completion note (Milestone 2.1.6 — Retry Classification and Backoff):** `platform_posts` gained `retry_count`/`next_retry_at` columns. Case A's requeue (`update_platform_post_if_unchanged(..., status="PENDING")`, crash_recovery.py:103-104) writes only `status`/`updated_at` and leaves `retry_count`/`next_retry_at` untouched — orthogonal by construction: a row is never simultaneously `PUBLISHING` (crash recovery's only target) and mid-retry-wait (a `PENDING` row with a future `next_retry_at`), since claiming (`PENDING` -> `PUBLISHING`) doesn't touch retry fields either. A row recovered from a crash that happened to be a retry attempt simply carries its prior `retry_count`/`next_retry_at` back into `PENDING`, and `due_post_selector`'s `next_retry_at` gate (see `content_store.get_due_platform_posts`) applies to it exactly as it would to any other `PENDING` row. No behavior change to crash recovery itself. See `docs/evaluations/scheduling/milestone-2.1.6-retry-backoff.md`.
