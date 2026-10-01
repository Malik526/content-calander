# ADR-0015: Hosted Scheduler Worker

## Status

Accepted (Milestone 3.12).

## Context

Hosted posts were scheduled and materialized as PENDING `platform_posts`, but nothing executed
them. Milestone 2.1.x already built and validated the local execution pieces: due selection,
the atomic claim (`claim_platform_post`), the single publish path
(`execute_claimed_platform_post`), retry/backoff, crash recovery, reconciliation and token
refresh. All of them are one-pass functions designed to be invoked repeatedly by a future
scheduler. 3.12 is that scheduler. Inspection found four gaps that kept those pieces from
running hosted unchanged.

1. **Credentials:** `TikTokPublisher` read the single local token file. Hosted connections
   are encrypted per user in `platform_credentials`.
2. **Timezones:** due selection compared the naive `scheduled_at` against now in the global
   `config.TIMEZONE`. Hosted slots are naive wall-clock times in each user's own cadence
   timezone.
3. **Media:** hosted uploads are never inspected at upload time (3.7), so validation crashed
   on NULL container metadata. The publisher's own `ffprobe` duration check also needs
   ffmpeg, which Railway's default Nixpacks image lacks. A storage failure while
   materializing escaped as a non-publish exception and left the claimed row PUBLISHING.
4. **Postgres requires `user_id`** for selection and claims, so a hosted pass must run per
   user.

## Decision

- **A separate long-running process** (`cli/run_worker.py` →
  `scheduling/hosted_worker.run_worker_loop`), deployed as its own Railway service
  (`railway.worker.json`), never inside FastAPI request handlers. Each cycle opens a fresh
  store and runs three existing one-pass jobs, in order, for every hosted user with PENDING
  or PUBLISHING work:
  1. crash recovery
  2. reconciliation
  3. `run_due_posts_once`
  The poll interval is configurable (`CONTENT_CALENDAR_WORKER_POLL_INTERVAL_SECONDS`,
  default 60s). SIGTERM/SIGINT stop new work between cycles.
- **Per-user credentials:** `TikTokPublisher` gains an optional `access_token_provider`.
  `publishing/tiktok/hosted_publisher.py` supplies one backed by `credential_store` for that
  user's connection. Missing or inactive connections → `REAUTHORIZATION_REQUIRED`;
  undecryptable credentials → `CREDENTIAL_UNAVAILABLE`. Both are terminal, existing
  classification. One publisher per user, used only for that user's rows.
- **Hosted eligibility = has a hosted login** (an `auth_identities` row). The local CLI
  bootstrap identity's rows (legacy `user_id=1` in production) stay owned by the CLI worker
  and are never claimed or failed by the hosted one, consistent with ADR-0012's decision to
  leave them untouched.
- **Timezone-exact due selection** (`scheduling/hosted_due_selection.py`):
  1. The existing selector over-selects with the furthest-ahead wall clock (UTC+14).
  2. Each row is then kept only if `scheduled_at` ≤ now in its own slot's timezone, and
     `next_retry_at` ≤ now in `config.TIMEZONE`, which is that column's existing storage
     convention (unchanged).
  `run_due_posts_once` gains an optional `due_posts` parameter, so claiming and execution are
  shared verbatim.
- **Media:** validation probes the materialized file (read-only `ffprobe`) when the video has
  no stored inspection. Materialization failures go through the existing
  `_schedule_retry_or_fail`:
  - `STORAGE_NETWORK_ERROR`: retryable.
  - `STORAGE_HTTP_ERROR`: classified by HTTP status.
  - Missing object / ownership mismatch: terminal.
  ffmpeg is added to the build (`nixpacks.toml`), and the worker refuses to start without it.
- **No new state machine.** Every transition is made by the existing functions; their
  semantics (claim, retry budget, missed-schedule behavior from 2.1.7, crash recovery from
  2.1.5, reconciliation from 2.1.10) are unchanged.

## Consequences

- Deploying the worker makes scheduled posts publish for real. Overdue PENDING posts of
  hosted users publish on the first cycle (2.1.7's existing missed-schedule behavior).
- Concurrency: multiple worker replicas are safe for publishing (atomic claim, tested against
  real Postgres). Run **one replica** anyway until 3.13: `credential_store`'s documented
  residual race (two simultaneous token refreshes for one connection) is more likely with
  several.
- Unresolved until 3.13: a crash after TikTok issued a publish ID but before it was persisted
  is indistinguishable from "never submitted". Crash recovery then requeues it (existing
  2.1.5 semantics), which can duplicate a post.
