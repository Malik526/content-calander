# Milestone 3.11 — User-Facing Publish States + Errors

## Objective

Make the real publishing lifecycle understandable in the Queue/Calendar. The goals are
Scheduled/Publishing/Published/Failed/Needs-attention states and safe failure explanations,
without leaking internals and without building retry/reconciliation machinery (3.13). Design
record: ADR-0013's 3.11 addendum.

## Existing Publish-State Architecture Found

- `platform_posts` already held the truth: `status` (PENDING/PUBLISHING/PUBLISHED/FAILED),
  `platform_post_id`, `published_at`, `retry_count`/`next_retry_at`,
  `next_status_check_at`/`status_check_count`, `updated_at`, and a free-text `failure_reason`.
- **Gap:** `failure_reason` is `str(exception)`. Some messages embed raw TikTok response bodies
  (`TikTok HTTP 4xx: {body!r}`), so it can't be shown, and parsing it would be fragile. The
  structured `PublishError.reason_code` existed but was never persisted. Local precondition
  errors (`PublishTikTokError`) had no code at all.
- 3.9's `display_status` lived inline in `api/routes/queue.py`, with a separate label/color
  map in each of `QueueSlotCard` and `QueueCalendarMonth`.
- There is still no hosted worker. A hosted PENDING post is never published, so past-due
  PENDING rows are a real, not hypothetical, state.
- Real local data (read-only check): 1 FAILED (caption too long, pre-3.11, no code),
  2 PUBLISHED (with IDs), 2 PENDING.

## What Was Built

**Backend**
- `platform_posts.failure_code` (nullable): SQLite on store open; Postgres
  `postgres_migrations/0009_add_platform_posts_failure_code.sql`.
- Written at every failure site in `scheduling/publish_tiktok.py` (submission, retry-pending,
  local preconditions `LOCAL_FILE_MISSING`/`CAPTION_MISSING`/`MEDIA_INCOMPATIBLE`/
  `STORAGE_UNAVAILABLE`, platform-reported `fail_reason`) and `scheduling/reconciliation.py`
  (terminal status-check errors). Status, retry and claim semantics are unchanged.
- `publishing/failure_taxonomy.py` + `publishing/tiktok/failure_codes.py`: code → category
  (`AUTH_REQUIRED`, `CAPTION_INVALID`, `MEDIA_INVALID`, `MEDIA_UNAVAILABLE`,
  `PLATFORM_REJECTED`, `RATE_LIMITED`, `TEMPORARY_PLATFORM_ERROR`, `NETWORK_ERROR`,
  `UNKNOWN_ERROR`) → fixed copy + action hint. The output never echoes its input.
- `publishing/publish_status.py`: the one resolver. Its docstring lists every
  NEEDS_ATTENTION trigger.
- `config.PUBLISH_OVERDUE_GRACE_MINUTES` (default 30).
- `media/caption_editing.post_locks_caption()`: the lock rule, now shared so `EDIT_CAPTION` is
  never suggested for a locked caption.
- `GET /api/queue/slots`, per slot: `display_status` (new value set), `reason_code`, `message`,
  `action_hint`, `published_at`, `can_unassign`, `publications[]` (per platform). Kept:
  `status`, `platform_post_status`, `assigned_video`. No `retryable` field, since no retry
  path exists to act on it (3.13).

**Frontend**
- `lib/status.ts`: `presentQueueStatus` (the one label/tone/dot map, shared by list card and
  calendar; anything unrecognized shows as "Needs attention") and `presentActionHint`.
- `QueueSlotCard`: badge, sanitized message, "Reconnect in Settings" link / "Edit the caption
  below.", and "Published <time>". "Remove from schedule" is now gated on `can_unassign`.
- New `attention` badge tone (two CSS tokens).

## Verification

- Backend `.venv/bin/python3 -m pytest` → **1024 passed** (was 956; +68), including the
  Postgres-backed suite with migration 0009.
  - `test_publish_status.py` (21): every state, every NEEDS_ATTENTION trigger, timezone-aware
    overdue logic, retry-pending note, lock-aware hints, multi-platform aggregation.
  - `test_failure_taxonomy.py` (30): code→category/hint table, copy, cross-platform code
    isolation, hostile codes never echoed.
  - `test_publish_tiktok.py` (+7) and `test_reconciliation.py` (+2): `failure_code` persisted
    on every failure path.
  - `test_api_queue_publish_status.py` (7): response shape, no raw text (token, log id,
    traceback, HTTP status, raw code) in the response body, pre-3.11 generic copy, overdue
    still removable, PUBLISHED identical across reloads with the unassign guard intact,
    contradictory PUBLISHED, cross-user isolation.
  - `test_postgres_content_store.py` (+1): `failure_code` round-trip.
  - **Three existing `test_api_queue.py` tests were updated, not weakened:**
    - `display_status` `"ASSIGNED"` is renamed `"SCHEDULED"`.
    - One used a now-past slot (2026-09-20), which 3.11 correctly reports as
      SCHEDULE_MISSED, so it moved to a future date.
    - Two marked a row PUBLISHED without a platform post ID, a shape the real publish path
      never produces, so they now set one. The contradictory shape has its own new tests.
- Frontend `npm run test` → **123 passed** (was 115; +8):
  - `queue-board.test.tsx` (+5): failed copy and reconnect link with no remove/retry, needs
    attention, published time, unknown status never raw or success, list/calendar
    consistency.
  - `status.test.ts` (+3).
  - One existing calendar test's selector changed from "assigned" to "scheduled".
  - `npm run lint` and `npm run build` are clean.
- Not done: no live deployed-stack check. Migration 0009 is not yet applied to production
  Postgres; it auto-applies when the deployed API opens a `PostgresContentStore`.

## Ambiguous States Discovered

- A hosted PENDING post past its time never publishes (no hosted worker). It is shown as
  NEEDS_ATTENTION / SCHEDULE_MISSED rather than "Scheduled".
- A pre-3.11 FAILED row has only free text, so it shows generic UNKNOWN_ERROR copy (this
  includes the one real legacy row, which was a caption-too-long failure).
- `HTTP_ERROR`/`PUBLISH_FAILED`/`TIKTOK_API_ERROR` can't be classified without the HTTP status
  (not persisted), so they map to UNKNOWN_ERROR.
- TikTok API/`fail_reason` code tables come from TikTok's public docs and were not all
  observed live. Unlisted codes are UNKNOWN_ERROR.
- A FAILED post with a platform post ID (accepted, then rejected) keeps the caption locked, so
  no "Edit caption" hint is shown.

## Deferred to 3.13

Retry and manual-retry endpoint, automatic retry/reconciliation for hosted posts, a hosted
worker/scheduler, recovery of stalled/unconfirmed/missed posts, duplicate-publish recovery,
a `retryable` API field, persisting HTTP status for finer classification, and backfilling
`failure_code` for pre-3.11 rows.
