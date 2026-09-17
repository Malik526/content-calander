# Milestone 2.1.4 — Worker Execution

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

**Update (Milestone 2.1.5, same day)**: the "Success / Failure Semantics" section below notes that a crash after a successful claim but before completion leaves a row stuck `PUBLISHING`, "expected and explicitly deferred to a future crash-recovery milestone." That milestone is done — see `docs/evaluations/scheduling/milestone-2.1.5-crash-recovery.md`. Such a row is no longer stuck forever: once stale (`config.PLATFORM_POST_STALE_MINUTES`), `crash_recovery.py` either requeues it to `PENDING` (if no `platform_post_id` was ever obtained) or resumes polling it (if one was), never resubmitting the media.

## Purpose

Prove that Pickle Batch can find a due job, take ownership of it safely, and execute the real publishing logic — without a human manually invoking the publish flow. Connects three pieces already proven in isolation (due detection, atomic claiming, the real TikTok publish flow) into one one-pass worker, and reconciles the ownership overlap Milestone 2.1.3 found and explicitly deferred.

## Worker Execution Flow

```
due_post_selector.get_due_posts()          (2.1.1, corrected in 2.1.2)
   -> ContentStore.claim_platform_post()   (2.1.3)
   -> publish_tiktok.execute_claimed_platform_post()   (2.0, extracted this milestone)
```

`worker.run_due_posts_once(store, publisher, platform="tiktok", now=None)`: discovers due `PENDING` rows, attempts an atomic claim on each, and for every successful claim executes the existing proven publish path. A failed claim (another process won first) is skipped, not retried or treated as an error. One pass only — no loop, no cron, no daemon; it discovers whatever is due right now, attempts each once, and returns a summary (`discovered`, `claimed`, `skipped`, `published`, `failed`, `errors`).

## Ownership Reconciliation

Investigated (not assumed) exactly how `publish_tiktok.py`'s `publish_video()` set `PUBLISHING` before this milestone: an unconditional `store.update_platform_post(record.id, updated_at=..., status="PUBLISHING")`, called after a plain Python-level eligibility check with no re-verification at write time — a real second, non-atomic ownership mechanism sitting alongside `claim_platform_post()`, exactly as Milestone 2.1.3 flagged.

**Phase 2 — separated claim from execute.** Extracted `execute_claimed_platform_post(store, video_id, platform, publisher)` from the back half of `publish_video()`. It assumes `status == PUBLISHING` already (ownership already established by the caller), validates the video, calls the publisher, persists `platform_post_id` immediately, and polls — the exact previously-proven Milestone 2.0 logic, verbatim, just no longer entangled with deciding whether to claim. Both `publish_video()` (after it claims) and `worker.py` (after it claims) call this same function — there is exactly one implementation of TikTok publishing logic, not two.

**Phase 3 — reconciled the manual CLI.** `publish_video()` now calls `store.claim_platform_post()` for its `PENDING -> PUBLISHING` transition instead of writing `status="PUBLISHING"` directly. One case needed care: a `FAILED` row that never obtained a `platform_post_id` (a true submission failure) was already, correctly, retryable by rerunning the CLI — but `claim_platform_post()` only transitions rows that are currently `PENDING`. Preserved that exact existing behavior by requeuing such a row to `PENDING` first, then claiming it through the same one real mechanism, rather than adding a second bypass path or silently making it non-retryable. Verified this preserves every pre-existing test unchanged (see Tests) — a `FAILED` row that already has a `platform_post_id` is unaffected (it's still caught by the earlier "already has a publish_id, never resubmit" branch, before any claim logic runs at all).

## Manual CLI Changes

`publish_tiktok.py`: `execute_claimed_platform_post()` added (new); `publish_video()` claims via `claim_platform_post()` instead of a plain `update_platform_post(status="PUBLISHING")`; a `FAILED`-with-no-`platform_post_id` row is requeued to `PENDING` before claiming. No CLI flags, arguments, or user-facing output changed. All 20 pre-existing `publish_tiktok.py` tests pass unmodified.

## Success / Failure Semantics

Unchanged from Milestone 2.0, now reached via `execute_claimed_platform_post()` for both callers: on a `PublishError` before a `platform_post_id` is obtained, `PUBLISHING -> FAILED` with `failure_reason` set, `platform_post_id` stays `NULL` (a true submission failure). On success, `platform_post_id` is persisted immediately — separately from, and before, the eventual poll outcome, which is what makes a crash between submission and polling safe. No retry/backoff, no stale-`PUBLISHING` recovery — a crash after a successful claim but before completion leaves that row `PUBLISHING`, which is expected and explicitly deferred to a future crash-recovery milestone, exactly per this milestone's guardrails.

## Idempotency

`UNIQUE(video_id, platform)` (Milestone 2.0) and `claim_platform_post()`'s atomicity (2.1.3) compose directly: a worker and a concurrent manual `publish_tiktok.py` invocation (or two concurrent workers) racing for the same row can only ever have one of them win the claim; the loser's `claim_platform_post()` call simply returns `False` and is skipped — never a duplicate submission, never an `IntegrityError`. Proven with a real two-thread, two-connection test running the **full** `discover -> claim -> execute` pipeline (not just the raw claim primitive, which 2.1.3 already proved) — `test_two_concurrent_workers_do_not_double_publish` — reran 10 times in isolation with zero flakiness: the shared `FakePublisher` was invoked exactly once every single time.

## Tests

`tests/test_publish_tiktok.py` (+2, 22 total): `test_publish_video_uses_claim_platform_post_not_a_plain_update` (spies on `claim_platform_post` to confirm it's actually called, not bypassed) and `test_publish_video_refuses_to_double_submit_an_already_claimed_row` (seeds a row already claimed by a simulated competing process; `publish_video()` must raise rather than resubmit).

`tests/test_worker.py` (new, 10 tests): a due `PENDING` post is discovered; it's atomically claimed; the publisher executes exactly once; successful execution ends `PUBLISHED` with `platform_post_id` persisted and the summary counts it; a failed execution ends `FAILED` and is counted/reported in `errors`; a row already claimed before discovery is neither discovered nor published (the simpler, deterministic half of "failed claim causes skip"); the two-thread/two-connection concurrent-workers test (the stronger, realistic half — an actual race, not just a pre-claimed row); a `PUBLISHED` post is not re-discovered or re-executed on a second pass; a `PUBLISHING` post is not treated as due work.

Result: **12 new/changed tests**, all passing (including 10/10 on repeated isolated runs of the concurrency test). Full suite: **477 passed** (465 prior + 12 new), no regressions.

## Local Validation

Ran the actual `worker.run_due_posts_once()` entry point (not just pytest) against an isolated temp SQLite database with one genuinely due `PENDING` TikTok post and a `FakePublisher`:

- `discovered=1`, `claimed=1`, `published=1`, `failed=0`
- `publisher.publish_calls` length: **1**
- final `platform_posts` row: `status=PUBLISHED`, `platform_post_id=pub_validation_1`

Exactly the expected outcome, driven entirely by the worker with no human invoking `publish_tiktok.py`.

## Real DB

Confirmed before and after this milestone's work: videos 3, 4, and 5 in the real `data/content.db` remain exactly `PENDING`, with `updated_at` timestamps byte-identical to the Milestone 2.1.2 backfill (`2026-09-17T03:53:50...`) — direct proof neither the worker nor any test touched production rows. Videos 1 (`FAILED`) and 2 (`PUBLISHED`) also unchanged. **No TikTok API calls were made** — every test and the local validation used `FakePublisher`.

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.4-worker-execution.md`. `docs/evaluations/scheduling/milestone-2.1.3-atomic-platform-post-claiming.md` updated with a dated note: the reconciliation it deferred is now done, and `claim_platform_post()` is the single ownership mechanism repository-wide.

## Conclusion

The invariant this milestone asked for is proven, not just implemented: a worker can discover a due job, safely take ownership of it via the same atomic primitive a concurrent process would race against, and execute the real, already-proven TikTok publish flow — end to end, with no human invoking `publish_tiktok.py`, verified against both a real local database and a genuine two-connection race. The ownership overlap Milestone 2.1.3 found is closed: there is now exactly one `PENDING -> PUBLISHING` mechanism, shared by the manual CLI and the worker, with zero duplicated publishing logic and zero regressions to the already-proven Milestone 2.0/2.1.2/2.1.3 behavior. No ADR was warranted — this composes four already-decided pieces via a narrow, conventional split (claim vs. execute) rather than introducing new architecture.

**Milestone 2.1.4: COMPLETE.**
