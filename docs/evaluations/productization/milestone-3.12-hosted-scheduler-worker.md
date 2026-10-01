# Milestone 3.12 — Hosted Scheduler + Worker Execution

## Objective

Make hosted scheduled posts execute automatically by reusing the validated local publishing
state machine: due work → atomic claim → existing publish pipeline → persisted result, plus
the normal retry/reconciliation handoff. Decisions: ADR-0015.

## Existing Worker Architecture Found

- One-pass, loop-free jobs: `worker.run_due_posts_once`, `crash_recovery.recover_stale_posts_once`
  and `reconciliation.reconcile_pending_status_checks_once`. Each already accepts `user_id`,
  `storage` and an injectable `now`, and they are run by thin CLIs against SQLite with the
  local token file.
- **Can run hosted unchanged:** atomic claim, publish path, retry classification/backoff,
  crash recovery, reconciliation, object-storage materialization, and the Postgres store
  methods for all of these.
- **Gaps** (ADR-0015 Context):
  - local-file credentials
  - one global timezone for due checks
  - hosted uploads never inspected, and no ffmpeg on Railway
  - storage errors escaping as non-publish exceptions
- Production state (read-only check): the real account (`user_id=2`) has 3 future PENDING
  posts (Oct 2, 5 and 9, 09:00 America/New_York). Legacy `user_id=1` (no login, no hosted
  credential) has 2 overdue PENDING posts.

## What Was Built

- `scheduling/hosted_worker.py`:
  - `run_hosted_cycle`: per hosted user, crash recovery → reconciliation → due posts;
    per-user error isolation; dry-run.
  - `run_worker_loop`: fresh store per cycle; quiet when idle; `once`; stop event.
  - `worker_prerequisite_problems` and signal handlers.
- `scheduling/hosted_due_selection.py`: timezone-exact due rows.
- `publishing/tiktok/hosted_publisher.py`: per-user token provider.
  `TikTokPublisher(access_token_provider=)`.
- `scheduling/worker.py`:
  - `run_due_posts_once(due_posts=)`
  - structured `log_event` lines: claim, start, succeeded, failed, retry scheduled,
    reconciliation scheduled, claim skipped
- `scheduling/publish_tiktok.py`:
  - ffprobe inspection for never-inspected media
  - materialization failures → existing retry/FAILED path
- `retry_classification`: `STORAGE_NETWORK_ERROR` retryable. `failure_taxonomy`: storage,
  inspection and credential codes.
- Store: `list_hosted_user_ids_with_platform_work` (both backends); Protocol entries for the
  worker's store calls.
- Entry point `cli/run_worker.py` (`--dry-run`, `--once`, `--poll-interval`).
  `railway.worker.json`; `nixpacks.toml` (ffmpeg). `config.WORKER_POLL_INTERVAL_SECONDS`
  (default 60).
- No schema migration, no `web/` change, no API change.

## Crash / Restart Behavior

| Worker dies… | State left | What happens next (existing semantics) |
|---|---|---|
| before claim | PENDING | Next cycle claims it normally. |
| after claim, before submission | PUBLISHING, no `platform_post_id` | After `PLATFORM_POST_STALE_MINUTES` (30), crash recovery requeues it and it publishes (tested). |
| during media materialization | same as above; temp file may remain in the container's temp dir | Same as above. The temp dir is ephemeral and cleared on restart. A materialization *error* (not a crash) now ends in retry/FAILED immediately (tested). |
| during the TikTok request, before a publish ID is persisted | PUBLISHING, no `platform_post_id` | Requeued and resubmitted after 30 min. **If TikTok had already accepted, this can duplicate the post. Unresolved; deferred to 3.13.** |
| after the publish ID is persisted, before the outcome | PUBLISHING with `platform_post_id` | Crash recovery/reconciliation only poll it, never resubmit (tested). |
| SIGTERM (redeploy) | — | Current cycle finishes, including an in-flight publish, then exit. If the platform's shutdown grace is shorter than the publish, see the rows above. |

## Verification

- Backend `.venv/bin/python3 -m pytest` → **1063 passed** (was 1027; +36).
- `tests/test_hosted_worker.py`: 17 behavior tests × {SQLite, real Postgres} + 2 Postgres-only.
  - due post claimed, materialized from storage (the file existed at publish time and the
    temp copy was removed) and published with the exact caption
  - future post untouched; per-slot timezone respected
  - retryable failure follows the existing backoff (not before `next_retry_at`, retried
    after)
  - terminal failure persists status and `failure_code`
  - accepted post handed to reconciliation and never resubmitted
  - stale unsubmitted claim recovered and published; stale submitted claim only polled
  - missing storage object → FAILED `STORAGE_OBJECT_MISSING` (not stuck)
  - undecodable upload → FAILED `CORRUPT_MEDIA`
  - per-user publisher isolation; no-login users left alone; one user's error doesn't stop
    others
  - dry-run writes nothing
  - real hosted publisher with no connection → FAILED `REAUTHORIZATION_REQUIRED` before any
    network call
  - hosted token provider (own credential / other user / rotated key)
  - loop runs one cycle
  - **Postgres:** 4 concurrent workers → exactly one publish; 6 concurrent store startups on
    an unmigrated schema → no migration race
- `cli/run_worker.py --dry-run --once` against the real stack (read-only): started,
  `users=1 due=0` (real account included; legacy CLI identity excluded; first real post is
  Oct 2), stopped cleanly.
- Not done: no deploy and no real TikTok publish (per brief). `nixpacks.toml`'s ffmpeg
  install has not been verified in a real Railway build. The worker's startup check reports
  it if missing.

## Manual Hosted Validation Checklist

> **Read first:** once the worker runs with `--dry-run` removed, the real account's scheduled
> posts will publish (privately, SELF_ONLY): Oct 2, 5 and 9 at 09:00 New York time, plus
> anything that becomes due. Unassign any you don't want published before deploying.

1. Create a second Railway service from this repo using config file `railway.worker.json`.
   Give it the same variables as the API service: `DATABASE_URL`, `STORAGE_BACKEND=supabase`,
   `SUPABASE_URL`, `SERVICE_ROLE_KEY`, `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`,
   `CREDENTIAL_ENCRYPTION_KEY`, `CONTENT_CALENDAR_TIMEZONE`. One replica.
2. First deploy with start command `python3 cli/run_worker.py --dry-run`. Confirm the logs show:
   - `event=worker_started`
   - no `worker_prerequisite_missing` (ffprobe present)
   - no migration errors in either service
   - `event=poll_cycle users=1 ...` every minute
3. Confirm the API still works after the worker deploy (migration race fixed; both services
   start safely).
4. Assign a test video to a slot a few minutes ahead, with a caption, and confirm TikTok is
   connected in Settings.
5. Switch the start command to `python3 cli/run_worker.py` (no dry run) and redeploy.
   Optionally set Railway's deployment draining time to ≥ 330s so a redeploy can finish an
   in-flight upload. Verify the setting name in Railway's docs.
6. At the scheduled time, confirm the logs show `post_claimed` → `publish_started` →
   `publish_succeeded` or `reconciliation_scheduled` for that `platform_post_row_id`.
7. Queue shows Scheduled → Publishing → Published, and `platform_posts.platform_post_id` is
   set.
8. The private post appears exactly once on the TikTok account. Logs show one `post_claimed`
   for that row and no second `publish_started`.
9. Confirm the legacy rows (`platform_posts` 5 and 6, `user_id=1`) are still PENDING and
   untouched.

## Deferred to 3.13

Resolving ambiguous submissions (the crash-during-request duplicate risk), stalled-claim and
missed-work recovery beyond the existing 30-min requeue, duplicate-publish detection/repair,
manual retry UX, a distributed lock for hosted token refresh (needed before running multiple
worker replicas), persisting ffprobe metadata for hosted uploads, and silent (no-audio) videos,
which the existing inspection rejects.
