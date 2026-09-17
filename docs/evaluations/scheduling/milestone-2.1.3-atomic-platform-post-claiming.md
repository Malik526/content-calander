# Milestone 2.1.3 — Atomic Platform-Post Claiming

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

**Update (Milestone 2.1.4, same day)**: the "Publisher Compatibility Finding" below deferred reconciling `publish_tiktok.py`'s own non-atomic `PENDING -> PUBLISHING` transition to a future milestone. That reconciliation is done — `publish_tiktok.py`'s `publish_video()` now claims through `claim_platform_post()` exactly like `worker.py` does. `claim_platform_post()` is now the single supported `PENDING -> PUBLISHING` ownership mechanism repository-wide. See `docs/evaluations/scheduling/milestone-2.1.4-worker-execution.md`.

## Purpose

Prove one invariant: for a given `platform_posts` row, at most one concurrent claimant may successfully transition it from `PENDING` to `PUBLISHING`. Claiming only — no worker loop, cron, actual TikTok publishing, retries, stale-job recovery, token refresh, or new infrastructure.

## Claim Contract

`ContentStore.claim_platform_post(post_id: int, updated_at: str) -> bool`. Returns `True` if this call performed the `PENDING -> PUBLISHING` transition (caller now owns the row), `False` if the row didn't exist or was no longer `PENDING`. Never raises merely because another claimant won first — a failed claim is an expected, normal outcome, not an error.

## Atomic SQL / Update Strategy

A single conditional statement:

```sql
UPDATE platform_posts SET status = 'PUBLISHING', updated_at = ? WHERE id = ? AND status = 'PENDING';
```

Claim success is read from `cursor.rowcount` (`1` = won, `0` = lost or missing) — never a separate `SELECT` beforehand. That's deliberate: a `SELECT status` then `UPDATE` in Python would let two callers both observe `PENDING` before either writes. A single `UPDATE ... WHERE ... AND status = 'PENDING'` is one indivisible SQLite write; two connections racing to claim the same row are serialized by SQLite's own file-level locking, so at most one `UPDATE` can ever still see `status = 'PENDING'` true — the loser's `WHERE` clause simply stops matching. No `BEGIN`/`COMMIT` wrapper was needed: `ContentStore`'s connection already runs in autocommit mode (`isolation_level=None`), and a single SQL statement doesn't need an explicit transaction to be atomic in SQLite. No Redis, advisory locks, or external queue — plain SQLite sufficiently provides the invariant this milestone requires.

## Ownership Semantics

Only `status` and `updated_at` are ever written by a claim. Verified by test that `platform_post_id`, `scheduled_at`, `published_at`, `failure_reason`, `video_id`, and `platform` are all byte-identical before and after a claim attempt (successful or not).

## State Transitions

- `PENDING` -> claim succeeds -> `PUBLISHING`
- `PUBLISHING` -> claim fails (already claimed)
- `PUBLISHED` -> claim fails (terminal)
- `FAILED` -> claim fails (terminal)
- nonexistent row -> claim fails cleanly, no exception

## Concurrency Validation

`test_concurrent_claims_produce_exactly_one_winner` uses two real, independent `ContentStore` instances (two separate `sqlite3` connections) against the same on-disk database file, from two real OS threads, synchronized with a `threading.Barrier(2)` so both attempt their `UPDATE` as close to simultaneously as possible — not a simulated race in a single connection. Asserted: exactly one `True` and one `False` result, and the row's final status is `PUBLISHING`. Re-ran this specific test 10 times in isolation (not just once as part of the suite) — passed all 10 times, supporting that this is genuine atomicity from SQLite's locking, not a result that happened to pass by scheduling luck.

## Publisher Compatibility Finding

Investigated `publish_tiktok.py`'s existing behavior (not assumed): `publish_video()` already performs its own `PENDING -> PUBLISHING` transition today, at line ~136 — `store.update_platform_post(record.id, updated_at=_now_iso(), status="PUBLISHING")` — called unconditionally once the surrounding `if` logic decides a row is eligible to (re)submit. This is a **plain, non-atomic** read-then-write: `publish_video()` reads the record via `get_platform_post()`, decides eligibility in Python, and only then writes `status="PUBLISHING"` with no re-check that the row is still `PENDING` at write time. Two concurrent manual invocations of `publish_tiktok.py` for the same video could both pass that check and both proceed to call `publisher.publish()`.

This is a **real, pre-existing overlap** with the new `claim_platform_post()` primitive — two different mechanisms currently exist for the same `PENDING -> PUBLISHING` transition, one atomic (new) and one not (existing). Per instruction, this milestone does **not** redesign `publish_tiktok.py` to use the new claim primitive — that would broaden this milestone beyond claiming-only and risk destabilizing the already-proven Milestone 2.0 manual publish path. `publish_tiktok.py` was not modified; all its existing tests continue to pass unchanged.

**Follow-up needed in a future worker-execution milestone (per the brief, "2.1.4")**: reconcile ownership so there is exactly one `PENDING -> PUBLISHING` mechanism, not two. The natural direction is for `publish_tiktok.py`'s own transition to be replaced by (or built on top of) `claim_platform_post()`, so the manual CLI and an eventual automated worker share one real ownership primitive instead of silently competing. Not implemented here — flagged as the next milestone's reconciliation work, exactly as instructed.

## Tests

Added to `tests/test_content_store.py` (colocated with the existing `platform_posts` method tests, matching that file's established pattern rather than a new file, since `claim_platform_post` is a single atomic primitive with no additional orchestration logic): 10 tests — claim succeeds and sets `PUBLISHING`; `updated_at` is set correctly; a second claim on the same row fails and does not overwrite the first claim's `updated_at`; a parametrized test confirms `PUBLISHING`/`PUBLISHED`/`FAILED` rows all reject a claim; a nonexistent row returns `False` cleanly (no exception); `platform_post_id` is preserved; `scheduled_at`/`published_at`/`failure_reason`/`video_id`/`platform`/`created_at` are all preserved; and the two-thread/two-connection concurrency test described above.

`due_post_selector.get_due_posts()` remains read-only and unmodified this milestone — already covered by the pre-existing `test_selector_has_no_side_effects` (Milestone 2.1.1), which continues to pass unchanged as part of the full suite; no new test was needed since nothing about that function changed.

Result: **10 new tests**, all passing (including 10/10 on repeated isolated runs of the concurrency test). Full suite: **465 passed** (455 prior + 10 new), no regressions.

## Real DB Validation

Before and after this milestone's work, videos 3, 4, and 5 (materialized `PENDING` in Milestone 2.1.2, scheduled 2026-09-19/21/23) were confirmed **still `PENDING`**, with `updated_at` timestamps unchanged from the 2.1.2 backfill (`2026-09-17T03:53:50...`) — direct proof `claim_platform_post()` was never invoked against production rows; all testing used isolated `tmp_path` databases. Videos 1 (`FAILED`) and 2 (`PUBLISHED`) also unchanged. **No TikTok API calls were made.**

## Conclusion

The core invariant the brief asked for is proven, not just implemented: two independent connections racing to claim the same row produce exactly one winner, verified by a real multi-thread/multi-connection test run repeatedly, not merely reasoned about. Ownership is now representable (`PENDING -> PUBLISHING` via `claim_platform_post`) without touching `scheduled_at`, `platform_post_id`, or any other field, and without disturbing the already-proven manual publish path. One real gap was found and explicitly deferred rather than silently patched or silently ignored: `publish_tiktok.py` still performs its own non-atomic `PENDING -> PUBLISHING` transition, and a future milestone must reconcile it with this new primitive so exactly one ownership mechanism exists. No ADR was warranted — this is a narrow, conventional SQLite conditional-update pattern, not a new architectural decision.

**Milestone 2.1.3: COMPLETE.**
