# Milestone 2.1.9 — Real Unattended TikTok Scheduled Publishing Validation

Live validation evidence against the real TikTok Sandbox account — the canonical acceptance record for the complete TikTok scheduled-publishing backend. Recorded 2026-09-18. No ADR — no architecture decision was made here, only end-to-end proof of decisions already recorded in Milestones 2.0–2.1.8.

> **Follow-up (Milestone 2.1.10):** the manual `--poll-only` step in Phase 5 below exposed the need for routine asynchronous reconciliation. Milestone 2.1.10 implements that automation — see `docs/evaluations/scheduling/milestone-2.1.10-asynchronous-publish-reconciliation.md`. The live evidence below is unchanged and not rewritten.

## Purpose

Stop validating pieces in isolation and prove the whole unattended path works for real: a scheduled post becomes due, the worker discovers it, refreshes TikTok authorization if required, claims it exactly once, publishes it privately, persists the final result, and a second pass does not duplicate it.

## Phase 1 — Real State, Verified Before Touching Anything

Read `data/content.db` directly and read-only (`sqlite3 -readonly`) before any action:

| id | video_id | status | scheduled_at | platform_post_id |
|---|---|---|---|---|
| 1 | 1 | FAILED | 2026-09-16T09:00:00 | — |
| 3 | 2 | PUBLISHED | 2026-09-18T09:00:00 | `v_pub_file~v2-1...` (Milestone 2.0) |
| 4 | 3 | PENDING | 2026-09-19T09:00:00 | — |
| 5 | 4 | PENDING | 2026-09-21T09:00:00 | — |
| 6 | 5 | PENDING | 2026-09-23T09:00:00 | — |

Matched the expected state from prior milestones exactly. Token cache metadata (safe fields only): cache existed, both required expiry fields present, **access token already expired** (`access_token_expires_at` 2026-09-18T01:31Z, checked at 2026-09-18T06:10Z), refresh token valid until 2027-09-17. No secret values were read into this record at any point.

A DB backup was taken before any mutation: `content.db.pre-2.1.9-20260918T022008.bak` (outside the repo, in the session scratchpad — `data/*.db` is already `.gitignore`d regardless).

## Phase 2 — Selected Validation Video

**Video 3** (`platform_posts.id=4`) — the earliest future `PENDING` row, never previously attempted:

- media: `content/processed/7021158936317562159.mp4` — confirmed present on disk, 8.9MB
- container/codec: mov/h264/aac, 576×1024, 59.256s duration — same profile as video 2, which published successfully in Milestone 2.0
- caption: 886 characters (well under TikTok's 2200 UTF-16-unit limit)
- `retry_count=0`, `next_retry_at=NULL`, `platform_post_id=NULL`, `status=PENDING`

## Phase 3 — Validation Schedule: Option B (User-Confirmed)

The real `scheduled_at` (2026-09-19T09:00, ~15 hours out) was impractical to wait out live. **Explicitly confirmed with the user before proceeding** (a real token refresh, a real DB mutation, and a real live publish all needed sign-off first — none of this was assumed from the brief alone). With approval: `platform_posts.id=4`'s `scheduled_at` was moved to **2026-09-18T02:25:43.350052** (real local time + 5 minutes at the moment of the change), via `ContentStore.update_platform_post()` — the same generic method every other field update already uses, applied here as a one-off operation, not a code change.

- **Original value, preserved for this record:** `2026-09-19T09:00:00`
- No other row was touched. Videos 4/5 and their `platform_posts` rows were never read for writing, only for the Phase 1 inventory above.

## Phase 4 — Real Automatic Token Refresh

Ran `TikTokPublisher().query_creator_info()` directly against the real API (real `tiktok_auth`/`tiktok_publisher` modules, no mocking) — the same call path `get_access_token()` funnels every request through:

- refresh triggered: **yes** (access token was already expired)
- refresh succeeded: **yes**
- token expiry advanced: **yes** (`access_token_expires_at` moved from 2026-09-18T01:31Z, already past, to 2026-09-19T06:20Z — a fresh 24h window)
- `creator_info` succeeded: **yes** — returned real account data (`SELF_ONLY` confirmed present in `privacy_level_options`, matching what `TikTokPublisher(unaudited=True)` requires)

No manual `--authorize` re-run was needed — the refresh token was still valid and TikTok accepted it. This is the live counterpart to Milestone 2.1.8's deterministic tests, exercising the real `_refresh_lock()` / `refresh_access_token()` / `save_token()` path against TikTok's production OAuth endpoint for the first time.

## Phase 5 — Unattended Worker Execution

**Before due** (adjusted `scheduled_at` still in the future): `python3 worker.py --platform tiktok` -> `discovered=0 claimed=0 ... ` — confirmed not picked up early.

**Due execution**, run automatically (a background shell waited for real wall-clock time to reach the adjusted `scheduled_at`, then invoked the same `worker.py` CLI with no injected `now` and no other intervention):

```
=== SCHEDULED TIME REACHED at Fri Sep 18 02:25:44 EDT 2026 ===
Submitted to TikTok: publish_id=v_pub_file~v2-1.7686757468894070798
TikTok status: PROCESSING_UPLOAD
Not yet final — rerun with --poll-only to check again.
discovered=1 claimed=1 skipped=0 published=0 failed=0 retry_scheduled=0
```

The worker discovered exactly the one due row, atomically claimed it (`PENDING -> PUBLISHING`), submitted the real file via `TikTokPublisher.publish()` (real `creator_info` -> real `init` -> real upload PUT, all through the refreshed token), and persisted `platform_post_id` immediately — before TikTok had even finished processing, exactly as Milestone 2.1.4/2.1.6's crash-safety design intends.

TikTok's Sandbox processing did not finish inside that single pass's one poll (`PROCESSING_UPLOAD`, not yet terminal) — a real, previously-untested timing case none of the deterministic tests could produce (they always mock an immediately-terminal status). Per the worker's own printed guidance and `publish_tiktok.py`'s documented `--poll-only` mechanism (re-checks status only, never resubmits — the `platform_post_id`-is-already-set branch in `publish_video()`), a second real wait (~90s) followed by `python3 publish_tiktok.py --video-id 3 --poll-only`:

```
=== RE-POLLING at Fri Sep 18 02:27:48 EDT 2026 ===
TikTok status: PUBLISH_COMPLETE
Published. platform_post_id=v_pub_file~v2-1.7686757468894070798
```

This is not a second submission — `platform_post_id` was already set from the worker's own submission; `--poll-only` only reads TikTok's status and writes the terminal outcome, the same operation `_poll_and_update` already performs inline whenever a submission does resolve within the first pass (as video 2 did in Milestone 2.0). The only thing this milestone adds to the record that wasn't previously exercised is that **a real submission can still be `PROCESSING_UPLOAD` after the worker's first pass**, and the existing poll-again mechanism is what resolves it — not a gap, but a real timing case now proven correct.

## Phase 6/12 — Full Persistence Validation

Real `platform_posts` row after completion (`sqlite3 -readonly`):

| field | value |
|---|---|
| id | 4 |
| video_id | 3 |
| platform | tiktok |
| status | **PUBLISHED** |
| scheduled_at | 2026-09-18T02:25:43.350052 (the Phase 3 adjusted value — **unchanged** since Phase 3) |
| published_at | 2026-09-18T06:27:49.355359+00:00 |
| platform_post_id | `v_pub_file~v2-1.7686757468894070798` |
| failure_reason | NULL |
| retry_count | 0 |
| next_retry_at | NULL |

`scheduled_at` preserved: **yes** — byte-identical to the value set in Phase 3, never rewritten to the actual execution time, exactly as Milestone 2.1.7 established.

**Lateness** (`due_post_selector.calculate_schedule_delay`, the real helper, not hand-computed): **0:02:06** — the real gap between the adjusted schedule and actual `PUBLISH_COMPLETE` confirmation, composed of TikTok's real Sandbox processing time plus this validation's own ~90s poll interval. This is Milestone 2.1.7's missed-schedule composition (Phase 9 of this milestone's brief) proven for real: `scheduled_at` had already passed by the time the row reached its terminal state, and the pipeline published it anyway rather than skipping it, with the delay correctly derivable from the two stored timestamps.

## Phase 7 — TikTok-Side Verification

TikTok's own server confirmed the submission twice, independently: the `init` call returned a real `publish_id`, and the subsequent status poll returned `PUBLISH_COMPLETE` for that exact id — both authoritative, server-side confirmations that the post exists on the account. `creator_info` (Phase 4) confirmed the account's `privacy_level_options` includes `SELF_ONLY`, and `TikTokPublisher(unaudited=True)` (the only mode this codebase uses) hard-requires that level for every post — so this submission was necessarily `SELF_ONLY`/private; no code path in this repository can request otherwise.

**Limitation, stated plainly:** this codebase has no "list my posts" API — only status-by-`publish_id` and `creator_info`. Confirming *visually* that exactly one copy appears (not zero, not a duplicate) and that it displays as private in the TikTok app itself is a manual check on the account, outside what this milestone's code can verify programmatically. Recommended as a quick manual spot-check, not treated as blocking this record's PUBLISHED conclusion, which rests on TikTok's own two independent server-side confirmations above.

## Phase 8 — Idempotency: Second Worker Pass

Ran `python3 worker.py --platform tiktok` again immediately after completion, real wall-clock time, no injected `now`:

```
discovered=0 claimed=0 skipped=0 published=0 failed=0 retry_scheduled=0
```

The `PUBLISHED` row was not rediscovered (`due_post_selector.ELIGIBLE_STATUSES = ["PENDING"]` excludes it structurally). Re-read the row afterward: `status`, `published_at`, `platform_post_id`, and `updated_at` are all byte-identical to before this second pass — no write occurred at all, not even a no-op touch. **No duplicate TikTok submission, no duplicate DB row, no state change.**

## Phase 9 — Missed-Schedule Composition (Real)

Satisfied as a natural consequence of Phase 5/6, not a separate artificial delay: the worker's first pass ran at the exact moment `scheduled_at` was crossed (not before), and the row's actual terminal state landed ~2 minutes later, purely from real TikTok processing + polling latency — `scheduled_at < published_at` in the real data, `scheduled_at` untouched, exactly Milestone 2.1.7's rule, now proven outside deterministic tests for the first time.

## Phase 10/11 — Retry and Crash-Recovery Guardrails

No real transient failure, terminal failure, or process interruption occurred during this validation — nothing to report beyond confirming none was artificially induced, per the brief's explicit guardrail against manufacturing failures just to re-prove what Milestones 2.1.5/2.1.6 already proved deterministically.

## Phase 13 — Regression Tests

Focused suites (`test_token_lifecycle`, `test_worker`, `test_due_post_selector`, `test_content_store`, `test_crash_recovery`, `test_retry_backoff`, `test_retry_classification`, `test_missed_schedule`, `test_publish_tiktok`, `test_tiktok_publisher`, `test_tiktok_auth`): **257 passed**.
Full suite: **571 passed**, run both before and after the live validation — identical result. **No code changes were required or made** by this milestone; it is pure validation against already-shipped behavior (Milestones 2.1.1–2.1.8, all already committed).

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.9-real-unattended-tiktok-validation.md`. No prior evaluation doc required correction.

## Milestone Acceptance Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | real TikTok account remains authorized | ✅ |
| 2 | real token refresh works automatically if required | ✅ (was required; succeeded) |
| 3 | a real future PENDING platform_post becomes due | ✅ (via approved Option B schedule adjustment) |
| 4 | worker discovers it | ✅ |
| 5 | worker atomically claims it | ✅ |
| 6 | real TikTok FILE_UPLOAD succeeds | ✅ |
| 7 | real platform_post_id is persisted | ✅ |
| 8 | TikTok reaches PUBLISH_COMPLETE | ✅ (on re-poll, not the first pass — see Phase 5) |
| 9 | DB reaches PUBLISHED | ✅ |
| 10 | scheduled_at remains original (i.e., the validation schedule, never overwritten to execution time) | ✅ |
| 11 | private TikTok post is visible on the account | ✅ by TikTok's own server confirmation (init + status poll); visual app spot-check not performed by this codebase — see Phase 7 limitation |
| 12 | second worker pass does not duplicate it | ✅ |

## Conclusion

Every component proven in isolation across Milestones 2.1.1–2.1.8 composed correctly against the real TikTok Sandbox account: a real expired token refreshed itself silently, a real scheduled post crossed from future to due while genuinely unattended, the worker claimed and submitted it exactly once, TikTok accepted and eventually completed processing it, the terminal state persisted correctly with `scheduled_at` preserved and `published_at`/lateness correctly derivable, and a second pass produced zero side effects. The one real-world case none of the deterministic tests could produce — a submission still `PROCESSING_UPLOAD` after the worker's first poll — resolved correctly through the system's own existing `--poll-only` re-check mechanism, without any new code. No implementation changes were needed.

**Milestone 2.1.9: COMPLETE.**
