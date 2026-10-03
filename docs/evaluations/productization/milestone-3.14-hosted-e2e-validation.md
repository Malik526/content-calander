# Milestone 3.14 — Full Hosted End-to-End Validation

**Status: IN PROGRESS.**

- **Confirmed in production:** a real hosted end-to-end publish (two posts, verified on
  TikTok).
- **Remaining:** the recovery, restart and 3.9 smoke-test runs, plus the post-fix Railway
  build check. See "Live Validation — Remaining".

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

### Follow-up: connected TikTok account identity (2026-10-03)

- **Why:** live validation could not tell which TikTok account had received the posts the
  worker resolved as PUBLISHED. Settings said only "Connected", and `account_label` was always
  null (3.6 rightly refused to show the opaque `open_id`).
- **Change:**
  - The status endpoint calls TikTok `creator_info` with the user's stored credential
    (`publishing/tiktok/creator_identity.py`) and returns `creator_username`,
    `creator_nickname` and `creator_avatar_url`.
  - Settings shows "Connected as @username".
  - A failed lookup leaves the connection reported as connected with null identity.
- **Next live step:** once deployed, Settings for `user_id=2` shows the actual handle. If it
  isn't the account being checked, that explains where the posts went. If it is, investigate
  TikTok's private-post (SELF_ONLY) visibility and status next.

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

## Live Run — Confirmed Real Hosted End-to-End Publish (2026-10-03)

**This was a real, confirmed hosted end-to-end publish, not just a backend status
assertion.** The posts were found on the connected TikTok account.

**Sequence** (account owner's Railway logs and TikTok check, and a read-only query of
production `platform_posts`):

1. The Railway hosted worker service was deployed and started with `dry_run=False`.
2. Its first cycle discovered two **overdue** PENDING posts for `user_id=2` (rows 25 and 26).
3. Both were atomically claimed (PENDING → PUBLISHING).
4. Both were submitted to TikTok, and TikTok returned a `publish_id` for each (persisted,
   checkpoint cleared).
5. Both stayed PUBLISHING with their `publish_id` while TikTok processed them (one inline
   status check each).
6. Reconciliation then resolved both as **PUBLISHED**.
7. The account owner found and verified both posts (private, SELF_ONLY) on the connected
   TikTok account.

Final production rows: PUBLISHED, `platform_post_id` set, `retry_count=0`,
`status_check_count=1`, `submission_state` NULL, no `failure_code`. There were no duplicates
(one row per video, one submission each).

| Row | Scheduled (ET) | Scheduled (UTC) | PUBLISHED at (UTC) | Late by |
|---|---|---|---|---|
| 25 | 2026-10-02 23:43 | 03:43 | 04:48:29 | 65 min |
| 26 | 2026-10-02 23:50 | 03:50 | 04:48:30 | 58 min |

`published_at` is when reconciliation confirmed completion. Both rows were resolved in the
same reconciliation pass, so they are 0.4s apart.

**Why both published even though they were late:** no worker existed at their scheduled
times. When it came online, both were PENDING with `scheduled_at` in the past, and due
selection has no lateness limit. See "Overdue Publishing" below.

## Build Secret Hardening (2026-10-03)

- **Symptom:** the worker's Railway build log showed Docker `SecretsUsedInArgOrEnv`
  warnings for `CREDENTIAL_ENCRYPTION_KEY`, `SERVICE_ROLE_KEY`, `TIKTOK_CLIENT_KEY` and
  `TIKTOK_CLIENT_SECRET`. **The log did not print secret values**; it named variables
  passed through `ARG`/`ENV`.
- **Root cause (configuration, not code):**
  - Both services used `"builder": "NIXPACKS"`.
  - Railway hands every service variable to the build. Nixpacks' generated Dockerfile
    then emits `ARG <name>` followed by `ENV <name>=$<name>` for every variable it
    receives. That's from `src/nixpacks/builder/docker/dockerfile_generation.rs`, which
    has no separate code path for secrets.
  - So each secret was available to every build step, and was written into the built
    image's ENV configuration.
  - Nothing in this repo needs a secret at build time: the build is `pip install -r
    requirements.txt && pip install .` plus ffmpeg. The secrets are read only by the
    running processes.
- **Exposure assessment:**
  - No value appeared in logs.
  - The values were stored in the image config of Railway's private deployment images,
    so anyone able to pull those images could read them. That's the same group that can
    already read them in the Railway dashboard.
  - Nothing indicates disclosure beyond that, so **no rotation**, per the brief.
  - Rotating anyway is cheap except for `CREDENTIAL_ENCRYPTION_KEY`: rotating that
    invalidates every stored TikTok connection.
- **Remediation:**
  - New repo-root `Dockerfile` shared by both services: `python:3.11-slim-bookworm`,
    apt `ffmpeg`, `pip install -r requirements.txt`, `pip install .`.
  - It declares **no `ARG`**. With Railway's Dockerfile builder, a service variable
    enters the build only if an `ARG` names it, so no secret enters the build. Railway
    still injects every variable into the container at runtime.
  - `railway.json` and `railway.worker.json` now use `"builder": "DOCKERFILE"`.
  - `nixpacks.toml` is removed (its only job was ffmpeg).
  - `.dockerignore` keeps `.env*`, `data/`, `content/`, `.venv` and `web/` out of the
    build context.
- **Start command change:**
  - Railway runs a Dockerfile service's start command in exec form, so `$PORT` would
    *not* expand. The API start command is now `python3 cli/run_api.py`, which already
    defaults `--host 0.0.0.0` and `--port $PORT` from the environment.
  - The worker's start command is unchanged.
- **Python version:**
  - Pinned to 3.11. Nixpacks installed an unpinned `python3` from its nixpkgs archive,
    so the production version wasn't visible from the repo.
  - Local tests run on 3.10. Every requirement publishes 3.11 wheels.
- **Verification without Docker** (none available in this environment):
  - `tests/test_deploy_config.py` (14 tests) guards: no `ARG`; no runtime-secret name in
    the Dockerfile; ffmpeg plus both pip installs present; both services on the Dockerfile
    builder; no `$` in start commands; `.dockerignore` entries; `run_api` binds to
    `$PORT` with no flags.
  - The exact build context (tracked files minus `.dockerignore` excludes) imports the API
    and worker with an empty environment and finds all 10 migrations. The worker's
    startup check then reports the missing runtime secrets, which shows secrets are
    needed only at runtime.
  - **Still needed on Railway:**
    - Both services build from the Dockerfile with **no `SecretsUsedInArgOrEnv` warnings**.
    - The worker logs `worker_started` with no `worker_prerequisite_missing`
      (ffprobe present, runtime secrets present).
    - API `/api/health` returns 200.
  - A failed Railway build leaves the running deployment in place.

## Overdue Publishing (evaluation + V1 decision, 2026-10-03)

### Current behavior, from code

- **Due rule** (`hosted_due_selection.get_hosted_due_posts` → `due_post_selector.get_due_posts`
  → `get_due_platform_posts`):
  - status `PENDING`
  - `scheduled_at IS NOT NULL`
  - `scheduled_at <= now` in the slot's own timezone (3.12)
  - `next_retry_at IS NULL OR next_retry_at <= now` in `config.TIMEZONE`
- **There is no maximum lateness, grace period, missed state or stale cutoff.**
  `scheduled_at` has no upper bound and is never rewritten. This was the deliberate,
  validated Milestone 2.1.7 "missed-schedule behavior" (catch up, record lateness via
  `calculate_schedule_delay`).
- `PUBLISHING`/`PUBLISHED`/`FAILED`/`UNKNOWN` are never due. Crash recovery handles stale
  `PUBLISHING`; manual retry handles `FAILED`/`UNKNOWN`.
- **Order and pacing:**
  - Oldest `scheduled_at` first (ties by id).
  - Within one cycle, each user's due posts run **sequentially, back to back, with no
    spacing**.
  - Users are processed one after another in the same cycle.
  - **No per-cycle batch limit and no spacing:** every due post is claimed in that cycle, and
    each publish (materialize, creator_info, init, upload, one status check) starts as soon
    as the previous one returns.
- **Inconsistency found:**
  - The Queue's display resolver (3.11, `publish_status`) already labels a PENDING post
    more than `PUBLISH_OVERDUE_GRACE_MINUTES` (30) late as NEEDS_ATTENTION /
    SCHEDULE_MISSED.
  - The worker still publishes it whenever it next runs.
  - Rows 25 and 26 would have shown "missed" while the worker was down, then published
    anyway.
- Tests pinning all of the above: `tests/test_hosted_overdue_semantics.py` (13).
- Backend suite after this follow-up: **1159 passed** (was 1127).

### Backlog and burst

| Worker down | 1 overdue | 5 overdue | 50 overdue |
|---|---|---|---|
| 10 min | publishes ≤10 min late; fine | all 5 back to back in one cycle | first ~6 publish, rest FAILED (see below) |
| 6 hours | publishes 6h late | 5 posts within ~1 min, up to 6h late | same as above |
| 3 days | publishes days late | 5 stale posts within ~1 min | same as above, plus the daily cap |

TikTok limits that decide the right-hand column:

- **Init rate limit:** `video/init` allows **6 requests per minute per user access
  token**, then HTTP 429 `rate_limit_exceeded`.
  - Our `retry_classification` treats that as **terminal** (an unrecognized code with a
    4xx status).
  - So in a burst, every post after the 6th in a minute ends **FAILED** with
    `rate_limit_exceeded`, which the UI shows as RATE_LIMITED with a "try again later"
    hint. They are not spread out.
- **Daily cap:** TikTok also enforces a daily post cap per creator (`spam_risk_too_many_posts`,
  HTTP 403, number not published), also terminal.
- **Duplicates:** neither limit causes duplicates (the claims are atomic). The failure mode
  is "stale content posted in a burst, then the rest FAILED", not double posting.
- **Proven in the tests:** `test_backlog_beyond_tiktoks_rate_limit_ends_failed_not_retried`
  shows 6 published and 2 FAILED.

### Policies considered

- **A. Automatic catch-up (current):**
  - Simple and durable; nothing is silently skipped, and downtime recovers by itself.
  - Risks: a post can go out hours or days after its intended time, a backlog posts back
    to back, and a backlog over TikTok's per-minute limit partly FAILS (see "Backlog and
    burst" above).
- **B. Grace window** (publish if late ≤ N, otherwise stop for a user decision):
  - It would hide short outages and stop stale bursts.
  - But any N (15 min, 30 min, 1 h, a fraction of the cadence gap) is a guess about what
    creators expect. Under a 30-min window, the two real posts above would *not* have gone
    out.
- **C. Never auto-publish overdue:**
  - Too brittle. Every deploy or restart overlapping a slot misses it, and the 60s poll
    interval alone makes every post a few seconds late.
- **D. User setting ("publish missed posts automatically / skip / ask me"):**
  - Plausible eventually.
  - Premature without evidence of what users actually want.

### V1 decision: keep automatic catch-up (provisional)

For V1, **current behavior is kept**: a PENDING, otherwise-eligible post that is overdue is
published when a worker next runs.

- **No grace window is added.** There is no real usage data yet on what creators expect
  when a scheduled post is missed, and any cutoff would be arbitrary.
- **This is a provisional product policy, not a durable architectural decision**, so it has
  no ADR. It is the existing Milestone 2.1.7 behavior, now explicitly re-affirmed for
  hosted V1 and pinned by `tests/test_hosted_overdue_semantics.py`.
- **Revisit with production data** from the telemetry below.
- **Known accepted risks, documented and not fixed:**
  - Stale posts can publish late.
  - A backlog publishes back to back.
  - Past TikTok's 6-inits-per-minute limit, the remainder end FAILED (`rate_limit_exceeded`,
    shown as "try again later", recoverable with Retry). There is no duplicate publishing.
  - The Queue shows SCHEDULE_MISSED for a PENDING post more than 30 min late, even though
    the worker will still publish it.
- **Flagged for the future policy decision** (not changed now):
  - Whether `rate_limit_exceeded` (HTTP 429) should be retryable. That would change 2.1.6's
    validated classification.
  - Whether the Queue's "missed" label should match whatever rule is chosen.

### Future missed-post UX (documented, deferred)

When real usage justifies it, the likely model is user-controlled recovery instead of a
silent rule:

> This post missed its scheduled time.
> Scheduled: Monday 9:00 AM
> **[Post now]** **[Reschedule]** **[Skip]**

A post can become overdue for different reasons, and the right default may differ per
reason. Where the reason is knowable, it should be shown:

| Cause | Knowable today? |
|---|---|
| Pickle Batch worker/service outage | Indirectly: a gap in `poll_cycle` logs / high `lateness_seconds` at claim with no `previous_failure_code` |
| TikTok connection unavailable / expired credentials | Yes: `REAUTHORIZATION_REQUIRED` / `CREDENTIAL_UNAVAILABLE` (FAILED, Reconnect hint) |
| TikTok outage, transient publish failure | Yes: retryable codes with `retry_count`/`next_retry_at`; `previous_failure_code` at the eventual claim |
| Platform rate/availability limit | Yes: `rate_limit_exceeded`, `spam_risk_*` |

Open product questions for that milestone:

- the default action (post vs. ask)
- whether "Skip" means unassign or a terminal state
- whether "Reschedule" picks the next OPEN slot (FIFO) or a chosen one
- whether the policy is per user or per cadence

### Overdue telemetry (added)

Structured log events (`event=... key=value`, ids, codes, timestamps and numbers only;
never tokens, credentials or captions):

- **`due_backlog`** — once per user per cycle with due work.
  - Fields: `user_id`, `platform`, `due`, `overdue`, `max_lateness_seconds`.
  - `overdue` counts due posts more than one poll interval late, i.e. ones a healthy,
    continuously running worker would already have claimed. This is a measurement
    definition, not a cutoff.
- **`post_claimed`** — gained `claimed_at`, `lateness_seconds` (claim time minus
  `scheduled_at`, in the slot's timezone, DST-correct) and `previous_failure_code` (why an
  earlier attempt didn't publish, if this is a retry). It already carried `scheduled_at`,
  `user_id`, `platform_post_row_id`, `retry_count`.
- **Outcome events** (`publish_succeeded` / `publish_failed` / `retry_scheduled` /
  `reconciliation_scheduled`) — gained `lateness_seconds`. Final resolution of an async
  post is `reconciliation_resolved` (same `platform_post_row_id`).
- **`dry_run_would_claim`** — gained `lateness_seconds`.

Questions the logs and data can now answer:

- **How often posts become overdue:** `due_backlog.overdue` over time.
- **How late they were when claimed:** `post_claimed.lateness_seconds`.
- **Backlog size at cycle start:** `due_backlog.due` / `overdue`.
- **Whether overdue posts ultimately published:** join `post_claimed` to outcome /
  `reconciliation_resolved` by `platform_post_row_id`. The database also keeps
  `scheduled_at`, the slot timezone and `published_at`.
- **Why delayed:** `previous_failure_code` / `retry_count` for failures; a `poll_cycle` gap
  for worker downtime.

Not added: an analytics table or dashboard. Logs plus existing columns are enough until a
policy decision needs aggregates.

Tests: `tests/test_hosted_overdue_semantics.py` (18). It pins the V1 semantics and checks
telemetry (lateness in the slot timezone, across a DST change, backlog counts, claim and
outcome lateness, retry cause, dry-run).

## Live Validation — Remaining

Done:

- The worker is deployed on Railway.
- The real publish was confirmed on TikTok (above).
- The connected-account display is committed (`acc3b73`).

Still to do:

1. **Post-fix build check:** commit and push the build-secret fix, then confirm:
   - Both services build from the `Dockerfile` with **no `SecretsUsedInArgOrEnv`
     warnings**.
   - The API `/api/health` returns 200.
   - The worker logs `worker_started dry_run=False` and no `worker_prerequisite_missing`
     (ffmpeg/ffprobe and runtime secrets present).
   - Quiet idle cycles at the 60s poll interval.
   - Settings shows "Connected as @…".
2. **Live frontend checks:**
   - Save a cadence and check the Queue shows the new slots without reloading.
   - Assign a video and let one more post publish on time. Expect the new `post_claimed
     lateness_seconds≈0..60`.
3. **Safe real recovery (FAILED → Retry → PENDING):**
   - Schedule a test post, then disconnect TikTok before its time.
   - The worker fails it with `REAUTHORIZATION_REQUIRED` before any TikTok call.
   - Reconnect, click **Retry**, and confirm it publishes once.
   - The UNKNOWN paths stay validated on disposable state only (tests). Manufacturing a
     real one is unsafe.
4. **Restart:** restart the worker while a post is PUBLISHING with a `platform_post_id`.
   Expect no second `publish_started`, then `reconciliation_resolved`.
5. **3.9 smoke test:** FIFO assign, manual assign, unassign, double-booking blocked, delete an
   unscheduled video, reload persistence, list/calendar consistency.

## Deferred to 3.15

Filled in when live validation completes.
