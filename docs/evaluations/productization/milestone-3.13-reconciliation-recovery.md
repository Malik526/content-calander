# Milestone 3.13 — Reconciliation + Recovery

## Objective

Harden the hosted worker against ambiguous and partial failure states without replacing the
publishing state machine. The milestone covers six areas:

- crash-during-request duplicate risk
- token-refresh coordination
- manual retry
- persisted media metadata
- silent videos
- explicit restart behavior per state

Decisions: ADR-0016.

## What Inspection Found

- **Duplicate path 1 (known, ADR-0015):** the `publish_id` was persisted only after
  `publish()` returned (after upload). A crash in between was requeued as "never submitted".
- **Duplicate path 2 (new, more likely):** an upload PUT that timed out after TikTok had the
  bytes raised `UPLOAD_NETWORK_ERROR`. That code is retryable, so the post was uploaded again.
- **TikTok API:** no idempotency key, no lookup without `publish_id`, `upload_url` valid one
  hour. Posting starts only after the upload completes. So the fix has to be ordering, not
  lookup.
- **Reconciliation:** marked an *accepted* submission FAILED on a terminal status-check error
  (e.g. reauth). That is a claim without evidence: it may have been published.
- **Token refresh:** CAS-only (ADR-0011's residual race). Two refreshers could both present
  the same rotating refresh token.
- **Silent video:** inspection rejected missing audio everywhere. TikTok's documented media
  requirements (format, codec, 23–60 fps, 360–4096 px, ≤10 min, ≤4 GB) contain no audio
  requirement.

## What Was Built

- **Submission checkpoint** (`scheduling/publish_tiktok.py`, `publishing/publisher.py`,
  `publishing/tiktok/publisher.py`):
  - `submission_state`/`submission_started_at` are written before `publish()`.
  - `TikTokPublisher` reports `publish_id` through `on_platform_post_id` *before* the upload
    PUT, and the id is persisted there.
  - An error after that point goes to reconciliation and is never retried as a fresh
    submission.
- **Crash recovery** (`scheduling/crash_recovery.py`):
  - NULL → requeue
  - `AWAITING_PLATFORM_ID` → bounded retry (`SUBMISSION_INTERRUPTED`)
  - `SUBMITTING` → `UNKNOWN`
- **Reconciliation** (`scheduling/reconciliation.py`):
  - A terminal check error → `UNKNOWN` (keeps the code and the id).
  - `STATUS_CHECK_MAX_ATTEMPTS` (default 150, about 24h) → `UNKNOWN` / `STATUS_UNRESOLVED`.
  - FAILED is written only on a platform-reported failure.
- **Manual recovery** (`scheduling/manual_recovery.py`, `POST /api/queue/slots/{id}/retry`):
  - Owner only, compare-and-swap.
  - Queue slots gain `can_retry` and `retry_requires_confirmation`.
  - `UNKNOWN` displays as NEEDS_ATTENTION / `OUTCOME_UNKNOWN`, with a `RECONNECT_ACCOUNT`
    hint when the cause was auth.
- **Token-refresh lock** (`credential_store.py`, `PostgresContentStore.credential_refresh_lock`):
  - Transaction-scoped `pg_advisory_xact_lock(hashtext(schema:connection_id))`, the same
    convention `postgres_migrate` uses.
  - The fast path (token still valid) takes no lock.
  - The lock holder re-reads first and reuses a token another holder just refreshed.
  - The lock is released on commit, rollback, or process death.
  - `lock_timeout` (`CREDENTIAL_REFRESH_LOCK_TIMEOUT_SECONDS`, 45) raises a retryable
    `CREDENTIAL_REFRESH_BUSY`.
  - SQLite: a no-op. The local CLI's file/fcntl path is untouched.
- **Media metadata:**
  - A successful publish-time ffprobe of a hosted upload is written back to the video row
    (container, codecs, width, height, fps, duration; file size if missing). Later attempts
    read it.
  - The write is best-effort: a failed write never fails a publish.
  - `TikTokPublisher` still runs its own pre-flight probe of the bytes it sends (duration
    check).
- **Silent video:** `inspect_media(require_audio=False)` on the publish path (job layer and
  `TikTokPublisher`). Local ingestion keeps requiring audio (it transcribes).
- **Structured logs** (ids/codes only):
  - `recovery_requeued`, `recovery_retry_scheduled`, `recovery_unknown`, `recovery_failed`,
    `recovery_polled`, `recovery_poll_failed`
  - `reconciliation_check_rescheduled`, `reconciliation_unknown`, `reconciliation_resolved`
  - `submission_outcome_pending_reconciliation`, `manual_retry`
  - `credential_refreshed`, `credential_refresh_reused`, `credential_refresh_lock_timeout`
- **Schema:** Postgres `0010_add_platform_posts_submission_checkpoint.sql`; SQLite additive
  columns. New status value `UNKNOWN` (no DDL).

## Restart / Reconciliation Behavior by State

| State on restart | Action |
|---|---|
| PENDING | Normal due selection and claim (unchanged). |
| PUBLISHING, no id, no checkpoint | Stale → requeue (unchanged). |
| PUBLISHING, no id, `AWAITING_PLATFORM_ID` | Stale → bounded retry via backoff; FAILED once the budget is spent. Provably no media sent. |
| PUBLISHING, no id, `SUBMITTING` | Stale → `UNKNOWN`. Manual recovery only. |
| PUBLISHING, with id | Status checks only: PUBLISHED / FAILED (platform-reported) / rescheduled; terminal check error or cap → `UNKNOWN`. Never resubmitted. |
| PUBLISHED | Untouched. |
| FAILED | Untouched; manual retry → PENDING. |
| UNKNOWN | Untouched by every automatic job; manual retry → re-check (with id) or, with confirmation, PENDING. |

## Verification

- Backend `.venv/bin/python3 -m pytest` → **1119 passed, 0 skipped** (was 1063; +56). The
  Postgres tests ran against real Postgres in disposable schemas.
- `tests/test_hosted_recovery.py`: 46 tests, behavior × {SQLite, Postgres} plus
  Postgres-only.
  - **Crashes:**
    - Crash after TikTok accepted `init` but before the id was persisted → no upload; bounded
      retry; exactly one media transfer in total.
    - Crash after the id was persisted → polled only.
  - **Ambiguous upload failure:** reconciled, never resubmitted. The real `TikTokPublisher`
    (HTTP mocked) already has the id on the row when the PUT starts.
  - **UNKNOWN handling:**
    - Unknowable submission → `UNKNOWN`, then ignored by later cycles.
    - Status never resolves → `UNKNOWN`.
    - Confirmation required for UNKNOWN without an id.
    - UNKNOWN with an id is re-checked, never resubmitted.
  - **Manual retry:**
    - Retry FAILED (fresh budget, history kept).
    - A platform-reported failure resubmits.
    - PENDING/PUBLISHING/PUBLISHED are rejected unchanged.
    - A stale snapshot loses its CAS.
  - **Media metadata and silent video:**
    - Metadata persisted and reused by the retry (ffprobe ran once).
    - A failed metadata write doesn't block the publish.
    - A silent video publishes; the real `TikTokPublisher` accepts a silent file; local
      ingestion still rejects one.
  - **Isolation and convergence:**
    - One user's recovery exception doesn't stop another user.
    - A restart over all 7 states converges, and further cycles change nothing.
  - **Postgres concurrency:**
    - 6 concurrent refreshers → exactly one refresh call; all six get the new token.
    - The lock is per connection (one held does not block another).
    - A failed refresh releases the lock immediately.
    - The lock wait is bounded → retryable `CREDENTIAL_REFRESH_BUSY`.
    - Logs carry no token values.
    - 5 concurrent manual retries → exactly one applied.
- **Negative control:** with the lock replaced by a no-op, the concurrent-refresh test fails.
  Six refresh calls present the same `refresh-1` token, confirming the test detects the race.
- `tests/test_api_queue_retry.py`: 10 tests:
  - retry FAILED
  - 409 for PUBLISHED/PUBLISHING/PENDING
  - confirmation flow
  - reconnect hint and re-check
  - owner only (404)
  - OPEN slot 404
  - 401 unauthenticated
- **Existing tests changed on purpose:**
  - Two reconciliation tests now expect UNKNOWN instead of FAILED on a terminal check error.
  - One hosted-worker test now expects persisted metadata.
  - The `inspect_media` stubs in two files accept the new keyword.
- **Not done:**
  - no deploy and no real TikTok call
  - no frontend change (see below)

## Deferred to 3.14

- Queue UI for retry. The API and the `can_retry`/`retry_requires_confirmation` fields are
  ready; a Retry button with a confirmation step for UNKNOWN is the remaining piece.
- A "Mark as published" recovery path for an UNKNOWN post the user finds live on TikTok
  (today: leave it UNKNOWN, or remove it manually).
- Keeping the previous `platform_post_id` on the row when a platform-reported failure is
  resubmitted (today it is logged by `manual_retry` only).
- Passing stored metadata into `TikTokPublisher` so it skips its own pre-flight probe.
- Live validation: deploy the worker (now safe to run multiple replicas) and exercise one
  real reconnect → UNKNOWN → retry cycle.
