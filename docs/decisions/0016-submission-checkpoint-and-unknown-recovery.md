# ADR-0016: Submission Checkpoint and UNKNOWN Recovery State

## Status

Accepted (Milestone 3.13).

## Context

ADR-0015 left one known duplicate-publish risk open. The `publish_id` was persisted only after
`publisher.publish()` returned, which is after the media upload. If the worker died in
between, the row looked "never submitted" and crash recovery requeued it. Inspection found a
second, more likely path to the same duplicate:

- An upload PUT that timed out *after* TikTok received the bytes raised
  `UPLOAD_NETWORK_ERROR`.
- That code is classified retryable, and `_schedule_retry_or_fail` assumes nothing was
  submitted.
- So the row went back to PENDING and was uploaded again.

TikTok's Content Posting API gives no way to recover a submission after the fact:

- There is no idempotency key.
- There is no endpoint that lists or looks up publish attempts without the `publish_id`.
- The `upload_url` is valid for one hour.
- With `FILE_UPLOAD`, "TikTok will start the posting process" only after the final chunk is
  uploaded (media transfer guide).

So recovery can't rely on lookup. It has to rely on the order in which things are persisted.

Separately, reconciliation marked an accepted submission FAILED when a status *check* hit a
terminal error (e.g. `REAUTHORIZATION_REQUIRED`). TikTok may well have published that post,
so FAILED was a claim made without evidence. With a manual retry, it would also invite a
duplicate.

## Decision

1. **Submission checkpoint.**
   - `platform_posts.submission_state` is written immediately before `publisher.publish()`
     and cleared once the outcome is known.
   - A `Publisher` may set `reports_platform_post_id_before_media_transfer = True`. It then
     receives an `on_platform_post_id` callback in `publish()` and must call it before
     transferring any media, transferring nothing if the callback raises.
   - `TikTokPublisher` calls it right after `init`, before the upload PUT. The callback
     persists `platform_post_id`.
   - Any `PublishError` after that point keeps the row PUBLISHING with its id and hands it to
     reconciliation. It never goes to `_schedule_retry_or_fail`, so it is never resubmitted.
2. **Crash recovery uses the checkpoint.** For a stale PUBLISHING row with no id:

   | `submission_state` | Meaning | Action |
   |---|---|---|
   | NULL | never reached the publisher | requeue (unchanged) |
   | `AWAITING_PLATFORM_ID` | no media can have been sent | bounded retry through the normal budget/backoff (`SUBMISSION_INTERRUPTED`), FAILED when exhausted |
   | `SUBMITTING` | publisher without the guarantee; outcome unknowable | `UNKNOWN` (`SUBMISSION_OUTCOME_UNKNOWN`) |

3. **New status `UNKNOWN`** is a parked state:
   - Due selection, crash recovery, reconciliation and the hosted worker's work list all
     ignore it.
   - Only the manual retry API releases it. The Queue shows it as NEEDS_ATTENTION /
     `OUTCOME_UNKNOWN`.
   - Reconciliation writes UNKNOWN instead of FAILED in two cases:
     - a terminal status-check error (`failure_code` stays the error's code, so "Reconnect
       TikTok" still shows)
     - a status that is still not terminal after `STATUS_CHECK_MAX_ATTEMPTS` checks
       (`STATUS_UNRESOLVED`)
   - The row keeps its id in both cases. FAILED with an id now means the platform itself
     reported failure.
4. **Manual recovery** (`scheduling/manual_recovery.py`, `POST /api/queue/slots/{id}/retry`):
   - FAILED → PENDING, as a fresh attempt.
   - UNKNOWN with an id → PUBLISHING for a status re-check. This never resubmits.
   - UNKNOWN without an id → requires explicit `confirm_not_published`.
   - PENDING, PUBLISHING and PUBLISHED are refused.
   - The transition is a compare-and-swap on `updated_at`, scoped to the owner.

Preference order applied: deterministic reconciliation > bounded retry > manual recovery >
blind resubmission (never).

## Consequences

- With `TikTokPublisher`, every way a crash can interrupt a submission now resolves
  deterministically: either the id is persisted (poll) or no media was sent (bounded retry).
  `UNKNOWN` from crash recovery only arises for publishers without the checkpoint, which
  today means only the test fakes.
- An abandoned `init` (crash before the id was persisted) leaves an orphan TikTok upload
  session that expires after an hour. It counts toward TikTok's pending-share limit
  (`spam_risk_too_many_pending_share`) until then. This is acceptable for a rare crash.
- Rows written before 3.13 have no checkpoint. A pre-3.13 PUBLISHING row with no id is still
  treated as "never submitted". The hosted worker had not been deployed before 3.13, so no
  hosted row is in that state.
- FAILED rows that already have an id and were written by pre-3.13 reconciliation might have
  been published. The manual retry treats FAILED-with-id as platform-reported and resubmits
  it. No hosted row predates 3.13 in that state either, for the same reason.
- Token refresh is coordinated separately (per-connection advisory lock, see
  `publishing/tiktok/credential_store.py`), which closes ADR-0011's residual race. Multiple
  worker replicas are therefore supported from 3.13.
