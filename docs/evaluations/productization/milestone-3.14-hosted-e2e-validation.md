# Milestone 3.14 — Full Hosted End-to-End Validation

**Status: IN PROGRESS.** The code fixes are done and verified locally. The live steps (deploy
these frontend changes, real publish, recovery, restart, smoke test) need actions only the
account owner can take. Those are listed under "Live Validation — Remaining" with the exact
steps.

## Code Changes

### Cadence save didn't refresh the Queue

- **Root cause:** `QueueBoard` loaded slots only on mount and when the month changed
  (`useEffect([accessToken, monthCursor])`). `QueueScheduling` saved the cadence, and the
  backend regenerated slots in the same request (~346 ms observed). But nothing told the
  board, so the new slots appeared only after a full reload. Its own docstring stated this:
  "QueueBoard picks up the regenerated slots on its own next load, not through any
  callback." This was a frontend invalidation defect, not a backend or latency problem.
- **Fix:**
  - `app/app/queue/page.tsx` holds a `queueVersion` counter.
  - `QueueScheduling` calls `onSaved` after a successful save (not after a failed one).
  - `QueueBoard` takes `refreshKey` and reloads slots and videos when it changes. The list
    and calendar views share that one state, so both update.
  - The cadence editor already updates from the save response.
- **Regression tests** (`tests/routes/queue-scheduling.test.tsx`):
  - Generated slots appear after save with no reload.
  - A failed save doesn't reload.
  - Confirmed the first test fails with the fix removed.

### Recovery UI (Retry)

- `QueueSlotCard` shows **Retry** only when `can_retry`.
  - Without `retry_requires_confirmation`, clicking it calls
    `POST /api/queue/slots/{id}/retry` directly.
  - With it, an inline warning appears first ("check your TikTok profile first — if it's
    already there, retrying will post it a second time"). Only the explicit "It isn't posted
    — retry" button sends `confirm_not_published=true`. Cancel backs out.
- `lib/api/queue.ts`: `retrySlotPublication`. `lib/api/types.ts`: `can_retry`,
  `retry_requires_confirmation`.
- **Latent 3.13 defect fixed:**
  - The retry endpoint returns `detail` as `{code, message}`, but `lib/api/client.ts` assumed
    a string, so a refused retry would have shown "[object Object]".
  - The client now reads structured details: the message is shown, and the code becomes
    `ApiError.reasonCode`.
  - On `CONCURRENT_UPDATE` the board reloads.
- **Tests:**
  - No Retry when `can_retry` is false.
  - A direct retry reloads the board.
  - The confirmation flow, including cancel.
  - A refused retry shows the backend's message.
  - The client parses structured details.

### Verification

- `web/`: `npm run test` → **130 passed** (was 123), `npm run lint` clean, `npm run build`
  passes (placeholder Supabase env).
- Backend unchanged by 3.14 code.

## Live Validation — Done (read-only, 2026-10-02)

| Check | Result |
|---|---|
| API health (`content-calander-production.up.railway.app`) | `200` |
| 3.13 API deployed | `POST /api/queue/slots/1/retry` unauthenticated → `401` (route exists) |
| Migration `0010` in production | Applied; `platform_posts.submission_state` exists |
| Hosted account state | `user_id=2`: TikTok connection ACTIVE with a stored credential; cadence active (America/New_York); 3 videos; 3 PENDING posts at 09:00 ET on Oct 5, 7, 9 |
| Legacy CLI rows | `user_id=1` posts 5/6 PENDING, post 1 FAILED, unchanged |
| Worker, `cli/run_worker.py --dry-run --once` against production (local process) | Started; no prerequisite missing; `users=1 due=0`; legacy identity excluded; stopped cleanly; nothing written |

Not determinable from here: whether the Railway **worker service** exists and is running. This
environment has no Railway or Netlify CLI or dashboard access. **If it is running without
`--dry-run`, the Oct 5 09:00 ET post will publish (privately) on its own.**

## Live Validation — Remaining

These need the account owner: deploying, Google login, uploading a real file, and approving a
real TikTok publish.

1. **Deploy:** push the 3.14 frontend changes (Netlify builds from `main`). Confirm the Railway
   worker service (ADR-0015 checklist in the 3.12 record) is running with
   `python3 cli/run_worker.py`. Logs should show `worker_started`, no
   `worker_prerequisite_missing`, and quiet idle cycles.
2. **Real flow** (production frontend):
   - Log in and check TikTok shows Connected.
   - Upload a short test video and confirm the Library row.
   - Add a posting time ~10 minutes ahead and save. **Check that the Queue shows the new slot
     without reloading.**
   - Assign the video to that slot (FIFO), and give it a caption.
   - At the slot time, expect these worker logs: `post_claimed` → `publish_started` →
     `publish_succeeded` or `reconciliation_scheduled` → (`reconciliation_resolved
     outcome=PUBLISHED`).
   - `platform_posts`: PENDING → PUBLISHING (`submission_state=AWAITING_PLATFORM_ID`) →
     PUBLISHING with `platform_post_id` (checkpoint cleared) → PUBLISHED.
   - Confirm exactly one private post on TikTok and one `post_claimed` for the row.
3. **Safe real recovery (FAILED → Retry → PENDING):**
   - Schedule a second test post, then disconnect TikTok in Settings before its time.
   - The worker fails it with `REAUTHORIZATION_REQUIRED` before any TikTok call (nothing is
     submitted).
   - Reconnect, click **Retry**, and confirm Scheduled, then published once.
   - UNKNOWN paths are validated only on disposable state (`tests/test_hosted_recovery.py`,
     `tests/test_api_queue_retry.py`, and the UI tests above). Producing a real UNKNOWN
     safely would need a deliberate failure mid-submission or an expired status check, which
     the brief says not to manufacture.
4. **Restart:** redeploy or restart the worker while a post is PUBLISHING with a
   `platform_post_id` (the window between upload and TikTok finishing). Expect no second
   `publish_started`, then `reconciliation_resolved` after restart. Restarting while the post
   is PENDING is trivially safe.
5. **3.9 smoke test:** FIFO assign, manual assign, unassign, double-booking blocked, delete an
   unscheduled video, reload persistence, list/calendar consistency.

## Deferred to 3.15

Filled in when live validation completes.
