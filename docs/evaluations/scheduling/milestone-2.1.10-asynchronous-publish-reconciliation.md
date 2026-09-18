# Milestone 2.1.10 — Asynchronous Publish Reconciliation

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-18.

## Problem Addressed

Milestone 2.1.9's live validation proved the entire unattended path works, with one manual step left in it: a real TikTok submission was still `PROCESSING_UPLOAD` after the worker's single inline poll, and a human had to run `publish_tiktok.py --video-id 3 --poll-only` to move it from `PUBLISHING` to `PUBLISHED`. This milestone removes that human step: any `PUBLISHING` row TikTok has already accepted (`platform_post_id` set) is now automatically re-checked on a backoff schedule until it reaches a terminal state — polling, not webhooks (explicitly deferred to Milestone 3's hosted productization).

## Phase 1 — Investigation

Traced `worker.py`, `publish_tiktok.py`, `tiktok_publisher.py`, `crash_recovery.py`, `content_store.py`, `publisher.py`, `config.py`, and the Milestone 2.1.9 record before writing anything. Confirmed:

- `platform_post_id` is persisted immediately after submission, separately from the eventual terminal outcome (`execute_claimed_platform_post`) — already crash-safe, already correct.
- `publish_tiktok.py`'s existing `--poll-only` path (`_poll_and_update`) already contained the exact TikTok-status -> DB-state mapping this milestone needs (`PUBLISH_COMPLETE` -> `PUBLISHED`, `FAILED` -> `FAILED`, anything else -> stay `PUBLISHING`) — reusable, not something to reinvent.
- **A second, independently-maintained copy of that same mapping already existed** in `crash_recovery.py`'s Case B (stale `PUBLISHING` + `platform_post_id` set) — same three branches, written separately, using `update_platform_post_if_unchanged` instead of `_poll_and_update`'s plain `update_platform_post`. Real duplication, confirmed by reading both side by side, not assumed.
- `crash_recovery.get_recoverable_platform_posts` is staleness-gated (`updated_at < now - PLATFORM_POST_STALE_MINUTES`, default 30 minutes) and is the *only* existing mechanism that ever revisits a `PUBLISHING` row — meaning, before this milestone, a submission that stayed `PROCESSING_UPLOAD` past the first poll would sit untouched for up to 30 minutes before crash recovery incidentally checked it again, or forever if a human never ran `--poll-only`. Confirmed by reading `crash_recovery.py`'s docstring and the Case B "still-processing" branch, which explicitly documents that it deliberately never refreshes `updated_at` on that branch — a design choice this milestone had to respect, not override (see Phase 7).

## Phase 2 — Reconciliation Contract

```text
PUBLISHING + platform_post_id IS NOT NULL -> eligible for reconciliation
```

Reconciliation only ever calls `publisher.get_status()` — never `publish()`, never `init`, never creates a `platform_posts` row. This is not a new invariant: it's the same "once `platform_post_id` exists, never resubmit" rule `crash_recovery.py` and `publish_tiktok.py` already enforce unconditionally, extended to a third caller. Enforced structurally (reconciliation.py has no code path that can reach `publisher.publish()` at all) and verified directly by test (`FakePublisher.publish()` raises `AssertionError` if ever called, across every reconciliation test).

## Phase 3 — Separate From Due-Post Selection

`due_post_selector.ELIGIBLE_STATUSES` is unchanged (`["PENDING"]` only). A new query method, `ContentStore.get_reconcilable_platform_posts`, is reconciliation's own selector — `status = 'PUBLISHING' AND platform_post_id IS NOT NULL AND (next_status_check_at IS NULL OR next_status_check_at <= now)`. The three selectors (`get_due_platform_posts`, `get_recoverable_platform_posts`, `get_reconcilable_platform_posts`) never overlap in practice: due selection is PENDING-only; crash recovery's Case A (no `platform_post_id`) and reconciliation are mutually exclusive by the `platform_post_id IS NOT NULL` condition; crash recovery's Case B and reconciliation can both see the same row, which Phase 7 addresses directly.

## Phase 4 — Poll Timing / Backoff

`config.STATUS_CHECK_BACKOFF_SECONDS = [30, 60, 120, 300, 600]` (env-overridable via `CONTENT_CALENDAR_STATUS_CHECK_BACKOFF_SECONDS`), indexed by a new `platform_posts.status_check_count` column — same shape as `RETRY_BACKOFF_MINUTES`/`retry_count` (Milestone 2.1.6), but **capped, not exhausted**: once `status_check_count` reaches the end of the list, further checks keep reusing the last (longest, 10-minute) interval indefinitely rather than ever giving up. This is a deliberate difference from the retry system: there is no retry-budget equivalent here, because nothing was ever resubmitted to "use up" — TikTok will eventually reach a terminal status for a submission it already accepted, so reconciliation has no reason to ever stop checking (verified by test — `status_check_count` far past the schedule's length still produces the last interval, not an error or an ever-growing one).

No tight loop and no sleeping process: each reconciliation pass checks whatever is currently due, once, and exits — the delay is enforced purely by `next_status_check_at` gating *selection* on a later, separately-invoked pass, exactly like `next_retry_at` already does for the retry system.

## Phase 5 — Invocation Shape

`reconciliation.py` is its own separate one-pass module with its own CLI entry point (`python3 reconciliation.py`), deliberately **not** folded into `worker.py`. This mirrors `crash_recovery.py`'s existing precedent exactly: `crash_recovery.py` is already a separate one-pass module with its own CLI, never called automatically from inside `worker.py`'s `main()` — this codebase's established pattern for "another concern a future scheduler invokes alongside due-post processing" is a sibling one-pass script, not a step folded into the worker. Following that same precedent here means `worker.run_due_posts_once()` is completely untouched (verified: `worker.py`'s diff for this milestone is zero lines), and a future cron/scheduler is expected to invoke `reconciliation.py`, `crash_recovery.py`, and `worker.py` in sequence, each a separate process, exactly as the brief's own preferred arrangement describes.

## Phase 6 — Shared Status Mapping (Duplication Removed)

Extracted `publish_tiktok._resolve_poll_outcome(status_result) -> (outcome, fields)` — a pure(-ish) function returning `("PUBLISHED", {...})`, `("FAILED", {...})`, or `("PROCESSING", {})` for a given `PublishStatusResult`. Three callers now share it:

1. `publish_tiktok._poll_and_update` (the inline poll right after submission, and the manual `--poll-only` CLI) — refactored to use it, and to additionally schedule the first automatic reconciliation check (`next_status_check_at`/`status_check_count`) when the outcome is still-processing, so a row never has to wait on a human running `--poll-only` even once.
2. `crash_recovery.py`'s Case B — refactored to call the same function for its `PUBLISHED`/`FAILED` branches, removing the previously-duplicated `if/elif` mapping. Its still-processing branch is **unchanged** (see Phase 7 for why).
3. `reconciliation.py` — the new routine path.

No behavior change for existing callers: `test_publish_tiktok.py` (with its `--poll-only` coverage) and `test_crash_recovery.py` both pass unchanged, confirming the refactor is behavior-preserving where it touches existing code.

## Phase 7 — Interaction With Crash Recovery

Kept deliberately separate, per the brief:

- **Reconciliation** is the routine path: scheduled, backed-off, always leaves a trail (`next_status_check_at`/`status_check_count` updated every time, even on a still-processing outcome).
- **Crash recovery** remains the safety net: staleness-gated (30 minutes of no activity), and its still-processing branch was **left completely unchanged** — it still deliberately does not touch `updated_at` (or, now, `next_status_check_at`) on that branch, exactly per its pre-existing comment ("refreshing it would reset the staleness clock and could delay the next recovery pass"). Reconciliation running routinely every ~30s–10min makes crash recovery's 30-minute staleness window essentially irrelevant for a healthy row in practice — but crash recovery's own behavior wasn't touched to make that true; it already worked correctly for its actual purpose (an abandoned/crashed claim), and this milestone didn't need to change it.

**Concurrency-safety, when both mechanisms can see the same row:** both write through `ContentStore.update_platform_post_if_unchanged` (the same optimistic-concurrency primitive Milestone 2.1.5 established), gated on the exact `updated_at` each caller last read. Verified directly by test (`test_crash_recovery_and_reconciliation_do_not_fight_over_same_row`): reconciliation's stale view of a row crash recovery already resolved fails its CAS write outright (`update_platform_post_if_unchanged` returns `False`), and the row is left exactly as crash recovery wrote it — never a torn or overwritten state.

## Phase 8 — Failure Handling

- **TikTok reports a terminal post failure** (`status_result.status == "FAILED"`): `status = FAILED`, `failure_reason` persisted, no further scheduling — the row leaves `PUBLISHING` entirely, so `get_reconcilable_platform_posts`'s `status = 'PUBLISHING'` filter excludes it from every future pass regardless of any stale `next_status_check_at`/`status_check_count` left on the row.
- **The status request itself fails transiently** (`publisher.get_status()` raises `PublishError`): the row stays `PUBLISHING`, and — unlike leaving it stuck at its old (now-elapsed) `next_status_check_at` forever — reconciliation explicitly reschedules the next check using the same backoff, so a transient network blip checking status doesn't strand the row in a tight "always immediately due, always immediately fails" cycle nor an indefinite one. Verified by test.
- **Authorization**: `publisher.get_status()` routes through the exact same `get_access_token()` path every other TikTok call already uses (Milestone 2.1.8) — a transient auth-refresh failure surfaces as an ordinary `PublishError` (retryable-shaped, same as any other transient status-check failure, handled identically above); a genuine `TikTokReauthorizationRequiredError` surfaces the same way — the row stays `PUBLISHING` with the error recorded, never silently marked terminal and never resubmitted. Verified with two integration tests using a real `TikTokPublisher` against mocked HTTP: one where a stale cached token silently refreshes mid-reconciliation (proving composition with 2.1.8, not just that both exist independently), and one where TikTok rejects the refresh outright.

## Phase 9 — Polling State Persistence

Two columns added to `platform_posts` via the existing additive-migration pattern (`_PLATFORM_POSTS_MIGRATION_COLUMNS`/`_ensure_platform_posts_columns`):

- `next_status_check_at TEXT` (nullable — `NULL` means "never scheduled yet," immediately eligible, the same convention `next_retry_at IS NULL` already uses)
- `status_check_count INTEGER NOT NULL DEFAULT 0`

**Timestamp convention: aware UTC**, matching `updated_at`'s convention — deliberately **not** `scheduled_at`'s naive-local-time convention, per the brief's own explicit instruction. This is the opposite convention choice from `next_retry_at` (Milestone 2.1.6), which deliberately matches `scheduled_at`'s naive-local time so `due_post_selector`'s single injected `now` could gate both clauses in one query. `next_status_check_at` has no such requirement — it never appears in the same query as `scheduled_at` at all (a completely separate selector), so it was free to match the convention that actually composes best with its own context: `updated_at`, `published_at`, and `get_recoverable_platform_posts`' `stale_before_iso` are all already aware UTC, and reconciliation's own `now` needs to be directly comparable to those, not to `scheduled_at`.

## Phase 10 — Tests

New file `tests/test_reconciliation.py` (23 tests): selector semantics (reconcilable vs. not, for every status and for missing `platform_post_id`), the `next_status_check_at` boundary (future/exact/elapsed), processing-keeps-`PUBLISHING`, first-interval scheduling, increasing backoff across repeated checks, the backoff cap, both terminal outcomes, `platform_post_id` immutability, `publish()` is structurally never callable, transient status-check failures (no resubmission, reschedules), two integration tests proving composition with Milestone 2.1.8's token refresh (silent refresh mid-check; reauthorization-required doesn't resubmit), a real-threads concurrency test (two reconciliation passes racing the same row — exactly one write applies), the crash-recovery/reconciliation non-corruption test, and a terminal row never being selected again.

Also reran the full suite to confirm the `publish_tiktok.py`/`crash_recovery.py` refactor changed no existing observable behavior.

Focused: `pytest tests/test_reconciliation.py tests/test_publish_tiktok.py tests/test_crash_recovery.py tests/test_worker.py tests/test_content_store.py -q` -> **128 passed**.
Full suite: `pytest` -> **594 passed** (571 prior + 23 new), no regressions.

## Phase 11 — Local Simulation

Ran directly via `reconciliation.reconcile_pending_status_checks_once()` against isolated temp SQLite DBs, `FakePublisher` throughout (`FakePublisher.publish()` raises if ever called, as an extra structural guard):

**Scenario A (processing then complete):** pass 1 -> `still_processing=1`, `next_status_check_at` = now+30s (the schedule's first interval); a pass run one second before that time -> `discovered=0`; a pass run exactly at that time, with a now-complete status -> `published=1`, `PUBLISHED`. `publish_calls=0`, `get_status_calls=2` throughout. **PASS.**

**Scenario B (processing multiple times):** three consecutive processing checks produced intervals of 30s, 60s, 120s — the schedule's first three entries, confirmed increasing — before a fourth check resolved to `PUBLISHED`. Exactly one `platform_posts` row existed throughout, `platform_post_id` stayed `"pub_stable"` the entire time, zero `publish()` calls. **PASS.**

**Scenario C (terminal TikTok failure):** one check with a `FAILED` status result -> `FAILED` immediately with `failure_reason` preserved; a pass run 30 simulated days later discovers nothing. **PASS.**

**Scenario D (transient status API failure):** a `PublishError` from `get_status()` leaves the row `PUBLISHING` with a rescheduled `next_status_check_at`; a later pass at that time, now succeeding, resolves to `PUBLISHED`. Zero `publish()` calls. **PASS.**

All four: **PASS.**

## Phase 12 — Real DB Guardrail

Opened the real `data/content.db` via `ContentStore()` (applies the additive migration, matching how the real CLI would run) and read the `platform_posts` table directly and read-only afterward. Migration applied correctly: `next_status_check_at`/`status_check_count` columns now exist, defaulting to `NULL`/`0` on all 5 existing rows. **Video 3 / `platform_posts.id=4` — the row Milestone 2.1.9 published live — is unchanged**: `status=PUBLISHED`, `scheduled_at`, `published_at`, and `platform_post_id` all byte-identical to the 2.1.9 record. No row was modified beyond the additive schema columns. **No TikTok API calls were made** by this milestone's implementation work — this was pure local development and testing.

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.10-asynchronous-publish-reconciliation.md`. A short follow-up note was added to Milestone 2.1.9's record (evidence there was not rewritten). No other prior evaluation doc required correction.

## Acceptance Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | `PUBLISHING` rows with `platform_post_id` are automatically revisited | ✅ |
| 2 | processing status causes a delayed future check | ✅ (30s/60s/120s/300s/600s, confirmed increasing) |
| 3 | no tight polling loop exists | ✅ (one pass, exits; delay enforced by selection gating, not sleeping) |
| 4 | `PUBLISH_COMPLETE` transitions to `PUBLISHED` automatically | ✅ |
| 5 | terminal TikTok failure transitions to `FAILED` automatically | ✅ |
| 6 | transient status-check failures are retried without resubmission | ✅ |
| 7 | `platform_post_id` is never replaced | ✅ |
| 8 | `publish()` is never called by reconciliation | ✅ (structurally guarded + tested) |
| 9 | `PUBLISHED`/`FAILED` rows stop being polled | ✅ |
| 10 | crash recovery remains a safety net, not the normal polling path | ✅ (its still-processing branch deliberately untouched) |

## Conclusion

The gap Milestone 2.1.9 exposed — a real submission that outlives the worker's single inline poll had no automatic path back to a terminal state short of a 30-minute crash-recovery sweep or a human running `--poll-only` — is closed with routine, backed-off, capped polling: a new selector deliberately kept separate from due-post selection and from crash recovery's staleness safety net, a shared status-mapping function that removed a real pre-existing duplication between `publish_tiktok.py` and `crash_recovery.py` rather than adding a third copy, and CAS-protected writes so reconciliation composes safely with both concurrent reconciliation passes and crash recovery touching the same row. No webhook infrastructure, no daemon, no busy loop — polling only, exactly as scoped. No ADR was warranted: this composes entirely from already-established patterns (the additive migration shape, the `update_platform_post_if_unchanged` CAS primitive, the capped-index-into-a-config-list shape `RETRY_BACKOFF_MINUTES` already established, and the separate-one-pass-CLI-module shape `crash_recovery.py` already established), not a new architectural decision.

**Milestone 2.1.10: COMPLETE.**
