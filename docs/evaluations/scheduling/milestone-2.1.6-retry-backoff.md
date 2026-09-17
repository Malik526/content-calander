# Milestone 2.1.6 — Retry Classification and Backoff

Validation evidence, not an architecture decision — no ADR was warranted (see Conclusion). Recorded 2026-09-17.

## Purpose

Answer: when a publishing attempt fails, was that a real, permanent problem, or did TikTok/the network just say "not right now"? Crash recovery (Milestone 2.1.5) answers "the program died." This milestone answers the other half: "the program stayed alive, but the attempt itself failed" — and, when that failure looks temporary, retries it a bounded number of times with increasing delay instead of giving up immediately or hammering TikTok in a loop.

## Failure Taxonomy

Every `PublishError` raise site in `tiktok_publisher.py` was inventoried (not assumed) via `grep -n "reason_code" tiktok_publisher.py publisher.py publish_tiktok.py`. Classified against the existing `reason_code` field — a machine-stable label that already existed for exactly this purpose, not an invented category:

**Retryable** (transient — worth another attempt): `NETWORK_ERROR`, `UPLOAD_NETWORK_ERROR`.

**Terminal** (will never succeed by retrying): `LOCAL_FILE_MISSING`, `CAPTION_TOO_LONG`, `CORRUPT_MEDIA`, `VIDEO_TOO_LONG`, `SELF_ONLY_UNAVAILABLE`, `UNSUPPORTED_PRIVACY_LEVEL`, `UNAUDITED_CLIENT_PRIVACY_RESTRICTION`, `AUTH_ERROR`.

**Dynamic/unrecognized** (`TIKTOK_API_ERROR`, `MALFORMED_RESPONSE`, `HTTP_ERROR`, `UPLOAD_FAILED`, or any future TikTok error code not yet in either list above): classified by the new structured `http_status` field — `5xx` is retryable (server-side, transient by convention), `4xx` is terminal (client-side, a resubmission would fail identically), and no `http_status` at all defaults **terminal**. This is a deliberate fail-closed default: an unrecognized failure with no structured signal is treated as permanent rather than retried blindly.

`PublishTikTokError` (local precondition failures — missing file, missing caption, incompatible container/codec) is unconditionally terminal by nature and is never routed through this classification at all; see `execute_claimed_platform_post`'s validation step below.

## `PublishError.http_status`

Added as a new optional field on the existing `PublishError` exception (`publisher.py`), populated only at the four `tiktok_publisher.py` raise sites where a real `requests.Response` was already available. This is genuinely structured data threaded from the actual HTTP response, not text-parsed or inferred from an exception message — satisfying the brief's requirement not to classify on generic exception text when structured information already exists.

One upload-path raise site was split during this inventory: the upload step's `except requests.RequestException` (a transport-level failure — connection refused, timeout, DNS failure) previously shared `reason_code="UPLOAD_FAILED"` with a genuinely different case (`upload_response.status_code not in (200, 201, 206)` — TikTok's upload endpoint responded, just with a bad status). The transport-failure case is the same transient nature as `NETWORK_ERROR` and now has its own code, `UPLOAD_NETWORK_ERROR`, so it classifies retryable independently rather than inheriting `UPLOAD_FAILED`'s (correctly terminal) classification.

## `retry_classification.py`

A pure predicate module — no DB or state access, mirroring the existing separation-of-concerns pattern (`due_post_selector.py` = pure selection, `platform_post_materializer.py` = orchestration):

```python
def is_retryable(reason_code: str, http_status: int | None = None) -> bool: ...
def classify(error: PublishError) -> bool: ...
```

Explicit-list membership (retryable or terminal) always takes priority over `http_status` fallback, in both directions — a code with a known terminal classification stays terminal even if it happened to carry a 5xx, and vice versa. This was verified directly by test, not just reasoned about.

## Persisted Retry State

`platform_posts` gained two columns via the same additive migration pattern `_ensure_videos_columns` established (`_PLATFORM_POSTS_MIGRATION_COLUMNS` + `_ensure_platform_posts_columns`, `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, called unconditionally from `ContentStore.__init__` so the same code path upgrades an existing DB or populates a fresh one):

- `retry_count INTEGER NOT NULL DEFAULT 0`
- `next_retry_at TEXT` (nullable)

`next_retry_at` deliberately uses the same **naive local-time** convention as `scheduled_at` (`slot_matcher.now_in_config_timezone()`), not `updated_at`'s aware-UTC convention — this lets `due_post_selector`'s single injected `now` gate both `scheduled_at <= now` and `(next_retry_at IS NULL OR next_retry_at <= now)` in one query without needing two different representations of "now."

## Backoff Policy

Centrally configured in `config.py`, not scattered magic numbers:

```python
RETRY_BACKOFF_MINUTES = [1, 5, 15, 30]  # env-overridable: CONTENT_CALENDAR_RETRY_BACKOFF_MINUTES
MAX_RETRY_ATTEMPTS = len(RETRY_BACKOFF_MINUTES)  # 4
```

`RETRY_BACKOFF_MINUTES[record.retry_count]` selects the delay before the *next* attempt — index 0 (1 minute) before the 1st retry, index 3 (30 minutes) before the 4th. `MAX_RETRY_ATTEMPTS` (the list's own length — one source of truth, not a separately configured ceiling that could drift out of sync) is the retry ceiling: a retryable failure is only scheduled while `retry_count < MAX_RETRY_ATTEMPTS`; once exhausted, an otherwise-retryable failure becomes terminal `FAILED` instead, preserving the final `failure_reason`.

## Orchestration — `publish_tiktok._schedule_retry_or_fail`

The one place classification and the retry budget are actually applied, called from `execute_claimed_platform_post`'s `except PublishError` handler (the shared path both the manual CLI and `worker.py` drive):

```python
if retry_classification.classify(error) and record.retry_count < MAX_RETRY_ATTEMPTS:
    delay_minutes = RETRY_BACKOFF_MINUTES[record.retry_count]
    next_retry_at = (now_in_config_timezone() + timedelta(minutes=delay_minutes)).isoformat()
    store.update_platform_post(
        record.id, updated_at=_now_iso(), status="PENDING",
        retry_count=record.retry_count + 1, next_retry_at=next_retry_at,
        failure_reason=str(error),
    )
else:
    store.update_platform_post(record.id, updated_at=_now_iso(), status="FAILED", failure_reason=str(error))
```

A scheduled retry returns the row to `PENDING` rather than writing `PUBLISHING` directly — it re-enters the normal `claim_platform_post()` ownership mechanism exactly like any other due work, the same design choice Milestone 2.1.5's Case A requeue made. `failure_reason` is always set, on the first retry just as much as the final exhausting one — never silently dropped.

## Due Selection

`ContentStore.get_due_platform_posts` gained one clause: `AND (next_retry_at IS NULL OR next_retry_at <= ?)`, with `now_iso` bound for both `scheduled_at` and `next_retry_at`. Ordering deliberately stayed `ORDER BY scheduled_at ASC, id ASC` — **not** `next_retry_at` — a deliberate decision: `scheduled_at` reflects the calendar-driven posting order that matters to the business, and a retry's internal backoff timing must never reorder that relative to other due content.

## Pre-Existing Bug Found And Fixed (In Scope)

While tracing every path into `execute_claimed_platform_post`, found that `_validate_ready_to_publish(video)` was called unwrapped: a `PublishTikTokError` from it (missing file, missing caption, incompatible codec — discoverable only after claiming, since `worker.py` has no earlier checkpoint) propagated uncaught, leaving the claimed row stuck `PUBLISHING` forever with no `failure_reason`. Crash recovery would eventually requeue it (no `platform_post_id` was ever set), it would get re-claimed, fail the identical validation again — an infinite loop for a permanently-broken video that never surfaced a visible terminal failure. Fixed by wrapping the validation call and marking the row `FAILED` immediately on `PublishTikTokError` before re-raising. Local validation failures are unconditionally terminal by nature, so this is a direct `FAILED` write, not routed through `retry_classification.py`. Covered by a dedicated test (`test_validation_failure_on_an_already_claimed_row_ends_failed_not_stuck`) proving the row now ends `FAILED` with a populated `failure_reason` instead of stuck `PUBLISHING`.

## Manual Retry vs. Automatic Retry

`publish_video()`'s pre-existing manual-CLI-rerun-of-a-`FAILED`-row path (a human explicitly reruns the CLI on a row with no `platform_post_id`) now also resets `retry_count=0, next_retry_at=None` on requeue — a manual intervention is a fresh, independent attempt, not a continuation of whatever the automatic retry budget had already accumulated. Verified by `test_manual_rerun_of_a_failed_row_resets_retry_state` (seeds accumulated retry state, then confirms a manual rerun both succeeds and resets both fields).

## `platform_post_id` Safety Invariant

Unchanged and unconditional: `_schedule_retry_or_fail` is only ever called from the `except PublishError` branch around the initial `publisher.publish()` call — i.e., only when TikTok never accepted the submission in the first place. Once `platform_post_id` is set, the row is `PUBLISHING`/`PUBLISHED` and is never routed through retry classification again; only polled. Verified directly by test (`test_published_row_with_platform_post_id_is_never_resubmitted`) and by every local validation scenario asserting `publish_calls` counts.

## Worker Behavior

`WorkerRunSummary` gained `retry_scheduled: int`. A single claimed post's retryable or terminal failure is caught inside the loop (`except PublishTikTokError`) and recorded — it never aborts the rest of the pass; every other due post in the same run is still attempted. Verified directly with a two-video batch where the first fails retryably and the second succeeds in the same pass (`test_worker_continues_batch_after_one_retryable_failure`).

## Tests

`tests/test_retry_classification.py` (new, 17 tests): known retryable/terminal codes (parametrized), `http_status` 5xx/4xx fallback for unrecognized codes, unknown-code-with-no-`http_status` defaults terminal, explicit-list priority over `http_status` in both directions, `classify()` reading a real `PublishError`.

`tests/test_tiktok_publisher.py` (updated, 38 tests total): `http_status` asserted on the upload-bad-status case; the upload transport-failure case's `reason_code` updated to `UPLOAD_NETWORK_ERROR`.

`tests/test_publish_tiktok.py` (+2, 24 total): one pre-existing test's failure reason_code was switched from `NETWORK_ERROR` (now correctly classified retryable, breaking its old FAILED-immediately assumption) to the genuinely terminal `CAPTION_TOO_LONG`, with its docstring clarified to describe the manual-retry pathway specifically; added `test_validation_failure_on_an_already_claimed_row_ends_failed_not_stuck` (the bug fix above) and `test_manual_rerun_of_a_failed_row_resets_retry_state`.

`tests/test_retry_backoff.py` (new, 16 tests): classification smoke checks (4), a single retryable failure persists `retry_count`/`next_retry_at` and returns to `PENDING` (1), `next_retry_at` matches the configured first backoff delay (1), a terminal failure ends `FAILED` immediately with `retry_count` untouched (1), a row awaiting a future retry window is not due / a row at or past its window is due — three boundary cases (3), a scheduled retry that succeeds ends `PUBLISHED` exactly once (1), `retry_count` increments correctly across multiple sequential retryable failures (1), retry exhaustion reaches `FAILED` with the final `failure_reason` preserved (1), the worker continues a batch after one retryable failure (1), a `PUBLISHED` row with a `platform_post_id` is never resubmitted even far in the future (1), and retry state persists across a fresh `ContentStore` reopening the same SQLite file (1).

Result: two new test files (`test_retry_classification.py`: 17 tests, `test_retry_backoff.py`: 16 tests), plus 2 new and 2 modified tests in `test_publish_tiktok.py` (now 24 tests) and 2 modified assertions in `test_tiktok_publisher.py` (now 38 tests). Full suite: **538 passed**, no failures, no regressions.

## Local Validation Scenarios

Ran outside pytest, via the real `worker.run_due_posts_once()` entry point against three separate temp SQLite DBs, `FakePublisher` throughout:

**Scenario A** (transient failure, retry, success): pass 1 (`NETWORK_ERROR`) -> row `PENDING`, `retry_count=1`, `next_retry_at` set, `platform_post_id` still `None`. A pass run one second later (before the retry window) discovers nothing (`discovered=0`) — confirming the row is not executed early. A pass run after the backoff window elapses, with a succeeding publisher, claims and executes exactly once -> `PUBLISHED`, `platform_post_id="pub_1"`, `publish_calls` length **1**.

**Scenario B** (terminal failure, immediate FAILED, never retried): one pass with `CAPTION_TOO_LONG` -> row `FAILED` immediately, `retry_count=0`, `next_retry_at=None`. A pass run 30 simulated days later discovers nothing (`discovered=0`) — a `FAILED` row is never picked up again, at any distance in time.

**Scenario C** (retry exhaustion): `MAX_RETRY_ATTEMPTS` (4) consecutive `NETWORK_ERROR` failures each scheduled another retry (`retry_count` climbing 1 -> 2 -> 3 -> 4, `status` staying `PENDING` each time); the 5th consecutive `NETWORK_ERROR` failure — with `retry_count` no longer below the ceiling — ended `FAILED`, `retry_count` unchanged at 4, and the final attempt's distinct `failure_reason` ("simulated blip #5") preserved verbatim. Exactly one `platform_posts` row existed throughout, `platform_post_id` stayed `None` the entire time — no duplicate rows, no accidental submission.

All three scenarios: **PASS**.

## Real DB

Opened the real `data/content.db` via `ContentStore()` (applies the additive migration, matching how the real CLI/worker would run) and read the `platform_posts` table directly and read-only afterward. Migration applied correctly: `retry_count`/`next_retry_at` columns now exist. All 5 existing rows carry the expected sane defaults, `retry_count=0` and `next_retry_at=NULL`, and every other field (`status`, `platform_post_id`, `scheduled_at`) is byte-identical to prior milestones' checks — video 1 `FAILED`, video 2 `PUBLISHED` (`platform_post_id="v_pub_file~v2-1.7686313615539865614"`, the genuine live TikTok Sandbox result from Milestone 2.0), videos 3/4/5 `PENDING`. No status, `failure_reason`, or `platform_post_id` was mutated on any row — only the additive schema columns were added. `data/content.db` is `.gitignore`d (confirmed via `git check-ignore -v`), so it is not part of any commit regardless. **No TikTok API calls were made.**

## Documentation

This record: `docs/evaluations/scheduling/milestone-2.1.6-retry-backoff.md`. A dated completion note was added to `docs/evaluations/scheduling/milestone-2.1.5-crash-recovery.md` clarifying that crash recovery's requeue and the new retry fields are orthogonal by construction (a row is never simultaneously `PUBLISHING` and mid-retry-wait).

## Conclusion

A publishing attempt that fails for a genuinely temporary reason — a network blip, a 5xx from TikTok — is no longer indistinguishable from a permanent one: it's classified using the `reason_code`/`http_status` data the codebase already produces, retried a bounded number of times with increasing delay, and only becomes a terminal `FAILED` once that budget is exhausted or the failure was never going to succeed in the first place. One failing job in a worker pass never aborts the rest of the batch, a row already holding a `platform_post_id` is never put anywhere near retry logic, and a human's manual rerun of a genuinely-failed row is treated as the fresh attempt it is rather than inheriting stale automatic-retry state. No ADR was warranted — this composes entirely from already-established patterns (the additive migration shape from `videos`, the `PENDING`-re-entry-through-`claim_platform_post` shape from crash recovery's Case A, the pure-predicate-module shape from `due_post_selector.py`), not a new architectural decision.

**Milestone 2.1.6: COMPLETE.**
