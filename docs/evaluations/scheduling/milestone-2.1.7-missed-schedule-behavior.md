# Milestone 2.1.7 — Missed-Schedule Behavior

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-18.

## Purpose

Answer: if a post is scheduled for 9:00 AM but the worker doesn't get a chance to run until 11:30 AM (offline, crashed, deploy window, whatever the cause), what happens to it? The product rule this milestone proves:

> If Pickle Batch misses the exact scheduled time, it does not forget the post. It publishes it as soon as it safely can.

## Phase 1 — Investigation: the current model already implements this

Traced `due_post_selector.py`, `content_store.get_due_platform_posts`, `worker.py`, `crash_recovery.py`, `retry_classification.py`, `publish_tiktok.py`, `config.py`, and the `platform_posts` schema before writing anything. Confirmed, not assumed:

- **`scheduled_at` is write-once.** `grep -rn "scheduled_at="` across every non-test `.py` file shows it is only ever passed at `insert_platform_post`/`insert_platform_post_if_absent` (materialization time, copied from the matched `content_slot`). No `update_platform_post`/`update_platform_post_if_unchanged` call anywhere in the codebase passes `scheduled_at` — it is structurally never rewritten after creation, not just rewritten-and-happens-to-match in today's code paths.
- **Overdue `PENDING` posts are already returned, unbounded.** `content_store.get_due_platform_posts` filters `scheduled_at <= ?` with no lower bound and no upper bound on how overdue a row may be — a row one minute overdue and a row three years overdue satisfy the same clause identically.
- **Retry timing (`next_retry_at`) is independent of the original schedule.** Added in Milestone 2.1.6, gated by its own `next_retry_at IS NULL OR next_retry_at <= now` clause, ANDed with (not substituted for) the `scheduled_at` clause. A row can be simultaneously "overdue by days" (via `scheduled_at`) and "not yet due" (via `next_retry_at`) — the two clauses compose independently.
- **No "late"/"missed" concept was persisted anywhere.** No status, boolean, or column existed for it prior to this milestone.
- **`published_at` already existed in the schema** (`content_store.py`'s `CREATE TABLE platform_posts`, present since Milestone 2.0/2.1.2 — not added by this milestone) and is written at actual execution time (`publish_tiktok.execute_claimed_platform_post` and `crash_recovery.recover_stale_posts_once`'s Case B), never backdated to `scheduled_at`.

Conclusion of Phase 1: this milestone needed no new due-selection logic, no new worker logic, and no new schema fields for the core policy — that already existed as an emergent property of `scheduled_at <= now` having no upper bound. The actual work was (a) proving it with tests at the actual "missed schedule" scale (hours/days, not the 1-hour distance existing tests already used), (b) a pure lateness-derivation helper for observability, (c) documenting the policy explicitly since nothing previously stated it in those terms, and (d) a real bug this investigation surfaced (see "Pre-Existing Bug Found and Fixed").

## Phase 2 — Policy (confirmed as already-implemented, not newly built)

```
PENDING + scheduled_at < now + retry window satisfied -> still eligible -> publish ASAP
```

Not a new retry (`retry_count`/`next_retry_at` untouched by this milestone). Not crash recovery (that handles a worker dying mid-job; this handles no worker having run at all). `scheduled_at` is preserved permanently; `published_at` reflects the real execution time, so lateness is always `published_at - scheduled_at`, computed on demand rather than stored.

## Phase 3 — No new persisted metadata

No new status and no new boolean flag were added. `scheduled_at` and `published_at` were already sufficient to derive lateness. The lifecycle remains exactly `PENDING -> PUBLISHING -> PUBLISHED`/`FAILED` — no `MISSED`/`LATE`/`OVERDUE` status was introduced.

## Phase 4 — Explicit overdue selection (verified, not just asserted)

`tests/test_missed_schedule.py` proves overdue selection at the distances the brief specifically calls out — 1 minute, several hours, and several days — where prior tests (`tests/test_due_post_selector.py`) only exercised 1 hour. Also proves the retry-gate composition explicitly at day-scale: a post overdue by a full day with a `next_retry_at` still in the future is correctly **not** due; the same row becomes due the instant `next_retry_at` elapses, independent of how overdue `scheduled_at` already was.

## Phase 5 — Lateness helper: `due_post_selector.calculate_schedule_delay`

Added as a pure function (no DB/state access, matching the module's existing "pure selection" shape):

```python
def calculate_schedule_delay(scheduled_at: str, published_at: str) -> timedelta: ...
```

**A real, pre-existing convention mismatch was found while building this**, not invented for it: `scheduled_at` is a naive local-time isoformat string (`config.TIMEZONE`), but `published_at` is an aware UTC isoformat string (`publish_tiktok`/`crash_recovery`'s `_now_iso()` — the same convention as `updated_at`, not `scheduled_at`). Subtracting the two raw strings either raises (`TypeError: can't subtract offset-naive and offset-aware datetimes` — reproduced directly by an early draft of this helper's own test) or, if tzinfo were blindly stripped instead, would silently misreport lateness by `config.TIMEZONE`'s UTC offset (roughly 4-5 hours for the default `America/New_York`). The helper converts `published_at` into `config.TIMEZONE` before subtracting, the same normalization `slot_matcher.now_in_config_timezone()` already applies elsewhere for the same reason. This is scoped strictly to the helper — no schema or write-path change; `published_at`'s on-disk representation is untouched.

Kept pure and derived, per the brief: no new column, no mutation, no row is ever labeled "late" in the database. A future UI can call it with a claimed row's `scheduled_at`/`published_at` to render "2h 32m late" without this milestone needing to build that UI.

## Phase 6 — Worker behavior (confirmed unchanged, verified by test)

`worker.run_due_posts_once` was already discover -> atomic claim -> execute with no special-casing for how overdue a row is — confirmed by reading (no branch anywhere inspects `scheduled_at`'s distance from `now`) and by test: `test_worker_executes_an_overdue_eligible_post` runs a real 3-day-overdue post through the actual worker entry point end to end. The worker does not skip, does not reschedule, does not mutate `scheduled_at`, and does not ask for confirmation — proven by `test_worker_does_not_modify_scheduled_at_on_overdue_publish` explicitly re-reading the row post-execution and asserting byte-identical `scheduled_at`.

## Phase 7 — Interaction with retry/backoff

All three cases from the brief reproduced, both via unit tests and the outside-pytest local simulation below:

- **Case A (offline system):** overdue, `next_retry_at IS NULL` -> executes immediately on the next pass.
- **Case B (previously failed transiently):** overdue, `next_retry_at` in the future -> not due until that window elapses, then executes normally, independent of how overdue the original `scheduled_at` already was.
- **Case C (terminal failure):** `FAILED` status is never selected regardless of how overdue `scheduled_at` is — `ELIGIBLE_STATUSES = ["PENDING"]` excludes it structurally, not by a time check.

## Phase 8 — Tests

New file `tests/test_missed_schedule.py` (13 tests). Deliberately does not duplicate coverage already proven elsewhere:

- 1-minute, several-hours, and several-days overdue `PENDING` rows are due (extends `test_due_post_selector.py`'s existing 1-hour-only coverage to the actual missed-schedule distances).
- Overdue `PUBLISHED`/`FAILED`/`PUBLISHING` rows stay excluded at day-scale overdue, not just 1-hour overdue.
- An overdue `PENDING` row with a future `next_retry_at` stays not-due; the same row becomes due once that window elapses (the exact-boundary/future/elapsed permutations of this already have full dedicated coverage in `tests/test_retry_backoff.py` — not re-duplicated here).
- `worker.run_due_posts_once` executes a genuinely overdue (3-day) post end to end: discovered, claimed, published.
- `scheduled_at` is byte-identical before and after an overdue publish.
- A successful overdue publish's `published_at` is strictly later than its `scheduled_at` once `calculate_schedule_delay` correctly normalizes the timezone mismatch (Phase 5).
- `calculate_schedule_delay`: zero delay for an on-time publish, correct positive delay for a late one, built against the real aware-UTC/naive-local convention split rather than two same-convention strings.

Focused: `pytest tests/test_missed_schedule.py -v` -> **13 passed**.
Full suite: `pytest` -> **551 passed** (538 prior + 13 new), no regressions.

## Phase 9 — Local missed-schedule simulation (outside pytest)

Ran directly via `worker.run_due_posts_once()` against isolated temp SQLite DBs, `FakePublisher` throughout, no pytest involved:

**Scenario A (simple offline-system overdue, `next_retry_at IS NULL`):** a post scheduled 2 hours before the injected `now`, `PENDING`. One pass: `discovered=1 claimed=1 published=1`. Final row: `status=PUBLISHED`, `scheduled_at` unchanged, `published_at` set to the real execution timestamp, `calculate_schedule_delay` returns a positive lateness.

**Scenario B (retry-gated overdue):** a post scheduled 2 hours before `now`, with `next_retry_at` 15 minutes in the future. Pass 1 (before the window elapses): `discovered=0`. Pass 2, run with `now` advanced 16 minutes: `discovered=1 published=1`. `scheduled_at` unchanged throughout.

**Scenario C (multi-day overdue):** a post scheduled 3 days before `now`, `PENDING`, no retry gate. One pass: `discovered=1 claimed=1 published=1`, `scheduled_at` unchanged.

All three: **PASS**.

## Pre-Existing Bug Found (Documented, Fixed Within Scope)

While building `calculate_schedule_delay`, an end-to-end test (`test_overdue_publish_preserves_original_scheduled_at_and_published_at_is_later`) raised `TypeError: can't subtract offset-naive and offset-aware datetimes` the first time it ran — not a test-authoring mistake, but a genuine latent convention mismatch between `scheduled_at` (naive local) and `published_at` (aware UTC) that had never previously been subtracted from each other anywhere in the codebase, so it had never surfaced. Fixed by normalizing `published_at` into `config.TIMEZONE` before subtracting, inside the helper itself (see Phase 5) — no schema change, no change to how `published_at` is written elsewhere.

## Real DB Guardrail

Read the real `data/content.db` directly and read-only via `sqlite3 -readonly` (deliberately not through `ContentStore()`, to guarantee zero risk of any write path executing — no migration was needed for this milestone regardless):

```
id|video_id|status   |scheduled_at        |published_at                    |retry_count|next_retry_at
1 |1       |FAILED   |2026-09-16T09:00:00 |                                |0          |
3 |2       |PUBLISHED|2026-09-18T09:00:00 |2026-09-17T01:43:20.029731+00:00|0          |
4 |3       |PENDING  |2026-09-19T09:00:00 |                                |0          |
5 |4       |PENDING  |2026-09-21T09:00:00 |                                |0          |
6 |5       |PENDING  |2026-09-23T09:00:00 |                                |0          |
```

All 5 rows match the state recorded at the end of Milestone 2.1.6, byte-for-byte. As of today (2026-09-18), none of the real `PENDING` rows (videos 3/4/5, scheduled 2026-09-19/21/23) are yet overdue, so this milestone's behavior was not exercised against real data — by design, per the guardrail. **No row was modified. No TikTok API calls were made.**

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.7-missed-schedule-behavior.md`. `due_post_selector.py`'s module docstring was updated in place with a Milestone 2.1.7 note clarifying that `scheduled_at <= now` intentionally has no upper bound. No prior evaluation doc required correction — 2.1.1/2.1.2's "due" definition and 2.1.6's retry-gating description were already accurate; this milestone only makes their unbounded-overdue implication explicit and tested.

## Conclusion

The existing pipeline already implemented the missed-schedule policy as an emergent property of `scheduled_at <= now` having no upper bound and never being rewritten — no new status, no new required schema field, and no worker special-casing were needed. This milestone's actual contribution is: test coverage proving that property holds at the distances (hours, days) the brief cares about rather than only the 1-hour distance prior tests happened to use; a pure `calculate_schedule_delay` helper for future lateness observability; and, in the course of building that helper, finding and fixing a real latent `scheduled_at`/`published_at` timezone-convention mismatch that had never previously been exercised. No ADR was warranted — nothing here is a new architectural decision; it composes entirely from `scheduled_at`'s pre-existing write-once/no-upper-bound contract, `published_at`'s pre-existing schema presence, and the pure-predicate-module shape `due_post_selector.py`/`retry_classification.py` already established.

**Milestone 2.1.7: COMPLETE.**
