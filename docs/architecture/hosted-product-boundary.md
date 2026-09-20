# Hosted Product Boundary — Target Architecture

Canonical architecture reference for turning Content Automation / Pickle Batch from a local
CLI tool into a hosted product. Written for Milestone 3.1 (hosted architecture boundary);
intended to guide Milestones 3.2–3.14, not just record this one. This document defines
*boundaries and responsibilities*, not a build plan — it does not schedule work, and it does
not select hosting providers except where a decision is genuinely unavoidable now.

No code changes accompanied this document at its original (Milestone 3.1) writing. See
`docs/evaluations/productization/milestone-3.1-hosted-architecture-boundary.md` for that
investigation record.

**Milestone 3.2 update (documentation-only edit to this file; see
`docs/decisions/0007-user-ownership-model.md` and
`docs/evaluations/productization/milestone-3.2-user-auth-ownership.md`):** the user/ownership
model §8–§10 described conceptually is now real — `users`/`auth_identities`/
`platform_connections` tables exist, `videos`/`content_slots`/`platform_posts` carry a
nullable `user_id`, and the four background job functions accept an optional `user_id` for
tenant-scoped execution. §8–§10 below are updated in place to describe the implemented model
rather than a future one; §6's persistence-boundary decision is updated to reflect that
`ContentStore`'s method surface was in fact extended (optionally, backward-compatibly) rather
than left untouched. Sections describing genuinely still-future work (Postgres, object
storage, real auth-provider integration, hosted credential storage) are otherwise unchanged.

**Milestone 3.3 update (see `docs/decisions/0008-postgres-persistence-migration.md` and
`docs/evaluations/productization/milestone-3.3-postgres-migration.md`):** Postgres is no longer
future work — `PostgresContentStore` is a real, tested, second implementation of the
persistence boundary §6 describes, running against real Supabase Postgres, with `user_id` now
`NOT NULL` there (the nullable-under-SQLite compromise §6/ADR-0007 describes was always scoped
to SQLite specifically). §6 and §14/§16 are updated in place. Object storage, real
auth-provider integration, and hosted credential storage remain exactly as future as before —
this milestone did not touch them.

**Milestone 3.4 update (see `docs/decisions/0009-object-storage-media-lifecycle.md` and
`docs/evaluations/productization/milestone-3.4-object-storage-media-lifecycle.md`):** object
storage is no longer future work — `storage.supabase_storage.SupabaseStorage` is a real,
tested implementation of the media boundary §7 describes, running against real Supabase
Storage, with the five real production videos migrated (local originals retained, never
deleted). `media/inspection.py` and `publishing/tiktok/publisher.py` were left unmodified,
exactly as §7's original prediction hoped for — the publishing materialization boundary lives
in `scheduling/publish_tiktok.py` instead. `media/processing.py` was **minimally extended
during review** (two optional hooks on `process_one`, plus a new
`process_storage_backed_video` entry point) after an initial pass left local CLI ingestion as
the only processing path, which contradicted this document's own "local files are only
temporary materializations" framing for a video that has actually been uploaded to object
storage — see ADR-0009's "Media processing" section for the correction record; local
ingestion's own behavior is unchanged, verified by the full pre-existing test suite. §7 and
§14/§16 are updated in place. Real auth-provider integration and hosted credential storage
remain exactly as future as before.

---

## 1. Current Local Architecture

```text
local CLI (cli/<name>.py, thin argparse wrappers)
    -> content_automation.* package (src/content_automation/)
        -> SQLite (persistence/content_store.py, ContentStore)
        -> local filesystem (content/incoming|processed|failed/, canonical_media_path)
        -> local token/config files (~/.config/content-calendar/*.json, data/calendar_state.json)
        -> TikTok Content Posting API v2 / Google Calendar API (direct HTTPS calls)
```

Every runtime concern already lives in `src/content_automation/<package>/` (Milestone 3.0's
package refactor); `cli/*.py` is argument parsing only. This matters for 3.1 specifically:
a future FastAPI route or job runner can call the exact same package functions the CLI calls
today — `content_automation.scheduling.worker.run_due_posts_once`,
`content_automation.media.processing.process_one`, etc. — with no CLI involved at all. That
was 3.0's explicit purpose ("Milestone 3.1 can now build on `content_automation.*` package
imports directly ... rather than needing to either shell out to scripts or untangle a flat
file layout first" — `docs/evaluations/productization/milestone-3.0-backend-package-refactor.md`).

**Major couplings found by this investigation** (all pre-existing, all deliberate, none
blocking):

- Exactly one SQLite database, one filesystem, one TikTok credential file, one Google
  Calendar OAuth identity, one dedicated Calendar — single-tenant by construction. Nothing in
  the code assumes multi-tenancy; nothing actively prevents adding it later (see §9, §11).
- `ContentStore` is the sole SQLite access point — verified by grep: no module outside
  `persistence/content_store.py` imports `sqlite3`. Every other package talks to persistence
  through `ContentStore`'s method surface only.
- `Publisher` (ABC) is the sole platform-publishing contract — `TikTokPublisher` is the only
  implementation; `scheduling/publish_tiktok.py`, `worker.py`, `reconciliation.py`, and
  `crash_recovery.py` all depend on the interface, never on TikTok specifics directly.
- All four scheduling background operations (`worker.run_due_posts_once`,
  `reconciliation.reconcile_pending_status_checks_once`,
  `crash_recovery.recover_stale_posts_once`, and — at the ingestion end —
  `media.processing.process_one`) are already "one pass, no daemon, no busy loop, plain
  parameters in/dataclass summary out" — the exact shape a hosted job runner needs to invoke
  them, requiring no adapter.

## 2. Target Hosted Architecture

```text
Web / future mobile client
          |
          v
       FastAPI  (src/content_automation/api/ — not yet built)
          |
          v
application/domain services  (content_automation.* package — already exists)
          |
   +------+-------------------+
   |                          |
   v                          v
persistence/infra         background jobs
adapters                  (media processing, scheduled publishing,
(Postgres, object         reconciliation, crash/stale recovery)
storage, credentials)          |
                                v
                         platform APIs (TikTok, Google Calendar)
```

Five layers, matching the brief's own preferred shape — deliberately not widened into a
DDD-style stack:

1. **API / transport** — FastAPI routes, request/response validation. Does not exist yet.
2. **Application/service layer** — orchestrates use cases. Largely already exists as the
   package's own public functions (see §4, §14) — this milestone did not need to invent a new
   `services/` module because 3.0 already produced one in substance.
3. **Domain/runtime logic** — scheduling, publishing, media logic. `scheduling/`,
   `publishing/`, `media/`, `calendar/` as they exist today.
4. **Persistence/infrastructure adapters** — Postgres (future), object storage (future),
   credentials, external APIs. `ContentStore`, `TikTokPublisher`, `tiktok/auth.py`,
   `calendar_manager.py` today; a Postgres-backed `ContentStore` and an object-storage
   adapter are the only genuinely new adapters this shape implies.
5. **Background jobs** — publishing, reconciliation, recovery, processing. Already exist as
   one-pass functions; only their *trigger* (cron today, a real scheduler later) changes.

## 3. Layer Boundaries And Responsibilities

| Layer | Owns | Must not do |
|---|---|---|
| API (future) | auth context, request validation, fast reads/writes, enqueueing jobs | long-running work (media processing, publishing, retry loops, reconciliation polling, crash recovery) |
| Application/domain | use-case orchestration, scheduling/publishing/media decisions | raw SQL, raw HTTP to TikTok/Calendar, filesystem paths (all via adapters) |
| Persistence/infra adapters | SQL dialect, object storage, credential storage, platform HTTP calls | business decisions (retry eligibility, slot matching, due-post selection) |
| Background jobs | invoking one-pass application functions on a trigger | inventing new business logic not already expressed in the application layer |

## 4. API Boundary: Synchronous vs. Asynchronous

**FastAPI should eventually handle synchronously** (per the brief, confirmed against the real
codebase — these are all either pure/fast or bounded I/O today):

- Authentication context, user-facing CRUD, account/settings.
- Upload *initiation* (accepting a file/reference and creating a `videos` row — not
  processing it).
- Schedule management: reads (`ContentStore.list_slots_by_status`,
  `get_due_platform_posts`-style queries), and schedule *generation* math
  (`calendar.generate_calendar.build_schedule`, `calendar.cadence.*` — pure, no I/O).
- Post/queue reads (`platform_posts`/`content_slots`/`videos` reads).
- Caption edits (`videos.caption_text` — a plain field write once "manual" caption editing
  exists; `media.caption.build_caption_from_transcript` itself is pure and fast).
- Integration connection *flows* — the request/redirect legs of TikTok/Calendar OAuth, not
  the connection's ongoing token refresh.

**FastAPI must not synchronously perform** (per the brief; confirmed as genuinely
long-running/blocking in the real implementations, not just asserted):

- Large video processing — `media.inspection.inspect_media`/`extract_audio` shell out to
  `ffprobe`/`ffmpeg` as subprocesses (30s/300s timeouts).
- Transcription — `media.transcription.FasterWhisperTranscriber` loads a local Whisper model
  and runs CPU inference; no bound on how long a video takes.
- Publishing — `publishing/tiktok/publisher.py` does a real multipart-style upload
  (`_UPLOAD_TIMEOUT_SECONDS = 300`) plus multiple sequential TikTok API calls per publish.
- Retry loops, reconciliation polling, crash recovery — these are explicitly designed as
  repeatable one-pass functions invoked on an interval, not requests a client waits on.

Those belong behind background-job boundaries (§5), invoked by a scheduler/queue, not by an
HTTP request/response cycle.

## 5. Background Job Boundaries

Four jobs already exist in substance; none needs new code to become "job-runner-callable" —
each is already a plain function taking `(store, publisher, ...)` and returning a summary
dataclass, with no daemon/loop/cron baked in.

| Job | Function | Consumes | Changes | Idempotent? | Conceptual trigger |
|---|---|---|---|---|---|
| Media processing | `media.processing.process_one` (+ `discover_videos`) | files in `content/incoming/` (future: an upload/object-storage queue) | `videos` lifecycle (DISCOVERED→VALIDATED→TRANSCRIBED/TRANSCRIPTION_FAILED→ASSIGNED/NEEDS_REVIEW/WAITING_FOR_SLOT/FAILED), `content_slots.status`, materializes `platform_posts` rows | Yes — sha256-content-hash-keyed, resumes from last completed stage (see module docstring) | Upload-completion event (future); currently a manual/cron CLI invocation |
| Scheduled publishing | `scheduling.worker.run_due_posts_once` | due `PENDING` `platform_posts` rows | claims via `claim_platform_post` (atomic UPDATE...WHERE), then row status/`platform_post_id`/`published_at`/retry fields | Yes — atomic claim + "never resubmit once `platform_post_id` is set" is unconditional | Schedule/interval (future cron/scheduler); currently manual/cron CLI |
| Reconciliation | `scheduling.reconciliation.reconcile_pending_status_checks_once` | `PUBLISHING` rows with `platform_post_id` set, due for a check | status/`published_at`/`failure_reason`/`next_status_check_at`/`status_check_count`, via `update_platform_post_if_unchanged` (optimistic concurrency) | Yes — status-only, never resubmits, capped (not exhausted) backoff | Interval, capped backoff per row |
| Crash/stale recovery | `scheduling.crash_recovery.recover_stale_posts_once` | `PUBLISHING` rows stale by `PLATFORM_POST_STALE_MINUTES` with no recent `updated_at` | Case A (no `platform_post_id`): requeue to `PENDING`. Case B (has one): poll-only, same outcome mapping as reconciliation | Yes — optimistic-concurrency guarded; Case B never resubmits | Interval, safety-net cadence |

**Potential later jobs** (not built, name-only, per the brief): transcription as its own
queued task (splitting it out of media processing once volume justifies a dedicated
worker/queue), analytics ingestion, historical import (in the shape of the existing
`cli/migrate_relocated_paths.py` one-off, generalized), caption generation (an LLM-based
enhancement — distinct from today's `caption.build_caption_from_transcript`, which only
normalizes whitespace and does not use an LLM).

### Multi-Tenant Execution Invariant

> **A hosted background job must never operate on one user's records using another user's
> credentials.**

**Milestone 3.2 update:** this is now implemented and proven, not only stated. `worker.
run_due_posts_once`, `reconciliation.reconcile_pending_status_checks_once`, and
`crash_recovery.recover_stale_posts_once` each accept an optional `user_id`; when supplied, it
is forwarded to the scoped selector/claim/update calls underneath (§6), so a pass scoped to
user B provably cannot discover, claim, or update user A's rows —
`tests/test_ownership.py`'s `test_worker_scoped_to_user_never_claims_other_users_post` (and the
matching reconciliation/crash-recovery tests) construct two real users with due/stale rows
each and assert the cross-tenant row is never touched. `media.processing.process_one` gained
the equivalent optional `user_id`, stamping every row it creates; a true per-user *storage
location* boundary (as opposed to per-user *row ownership*, which is what 3.2 delivers) is
still §7's future object-storage work, not this milestone's.

Every real CLI entry point (`cli/worker.py`, `cli/reconciliation.py`,
`cli/crash_recovery.py`, `cli/process_content.py`) resolves
`ContentStore.get_or_create_local_user()` and passes that id through, so real production
invocations are already scope-explicit today, even though only one real user exists. What
remains open, and is explicitly not this milestone's job to decide (see ADR-0007's
"Consequences"): **making `user_id` non-optional** once a real multi-connection
scheduler/job-runner exists that can always supply it, and whether the credential itself
(§8) — not just the row-ownership scope — needs to become per-connection before a second real
user can safely go live. A credential/data leak across accounts remains a security defect, not
a missing convenience feature; any future job redesign should be checked against this same
invariant with the same kind of explicit cross-tenant test, not merely reasoned about.

This invariant does not change persistence or concurrency semantics (§6) — atomic claim and
optimistic concurrency remain correct and necessary *within* one owner's scope; the invariant
is about which rows/credentials a given job invocation is allowed to reach in the first place.

No queue system was implemented or selected this milestone (explicit guardrail).

## 6. Persistence Boundary

**Milestone 3.3 update:** Postgres is implemented — `persistence/postgres_content_store.py`'s
`PostgresContentStore` is a real second implementation of the method surface described below,
tested against real Supabase Postgres (`tests/test_postgres_content_store.py`). See
ADR-0008 for the full architecture decision (why two parallel concrete classes rather than a
shared abstraction, the driver/migration-framework/timestamp/RLS decisions) and the Milestone
3.3 evaluation record for validation evidence. `ContentStoreProtocol`
(`persistence/protocol.py`) now exists as the documented shared contract — a `typing.Protocol`,
not a base class; neither concrete store inherits from it or from each other. One real
divergence from what §6 originally anticipated: Postgres's `user_id` columns are `NOT NULL`,
not optional like SQLite's — see "Ownership" in ADR-0008 for why the nullable compromise below
was always SQLite-specific, not a shape Postgres needed to inherit.

**Current boundary (as of Milestone 3.1's original writing — SQLite side unchanged): already
clean.** `ContentStore` (`persistence/content_store.py`) is
verified (via `grep -rl "^import sqlite3\|^import sqlite3 as" src/ cli/ tools/`) to be the
only module that imports `sqlite3` anywhere in the runtime package or CLI. Every other module
— `scheduling/*`, `publishing/*`, `media/*`, `calendar/*` — reaches persistence exclusively
through `ContentStore`'s public methods (`get_video`, `assign_slot`, `claim_platform_post`,
`get_due_platform_posts`, `update_platform_post_if_unchanged`, etc.). No SQL string is built
outside `content_store.py`.

**SQLite-specific behavior found — all contained inside `ContentStore` itself, none leaked
upward:**

- `PRAGMA foreign_keys`, `PRAGMA legacy_alter_table` (used specifically to prevent SQLite's
  automatic cross-table FK-reference rewrite during a `RENAME TABLE`-based migration).
- `sqlite3.Row` row factory, `isolation_level=None` with explicit `BEGIN IMMEDIATE`/`COMMIT`/
  `ROLLBACK`, `executescript` for schema creation.
- Two rebuild-based migrations (`_migrate_content_slots_unique_constraint`,
  `_repair_videos_assigned_slot_fk`) that exist only because SQLite cannot alter a table's
  constraints or FK targets in place — Postgres can (`ALTER TABLE ... ADD CONSTRAINT`,
  `ALTER TABLE ... DROP CONSTRAINT`), so these specific functions have no Postgres
  equivalent need, not just a portable one.

**What *is* portable as-is — confirmed, not just anticipated:** the two concurrency primitives
every scheduling module depends on — `claim_platform_post` (atomic `UPDATE ... WHERE status =
'PENDING'`, success read from `rowcount`) and `update_platform_post_if_unchanged`
(optimistic-concurrency `UPDATE ... WHERE updated_at = ?`) — ported to
`PostgresContentStore` unchanged in concurrency shape. Milestone 3.3 verified this under real
concurrent Postgres connections, not assumed parity: five real threads with five independent
Postgres connections racing to claim the same row (exactly one wins, four lose cleanly — no
exception, `rowcount = 0`), and a real stale-CAS-update-cannot-overwrite-newer-state proof (two
sequential real connections, the second holding an already-superseded `expected_updated_at`).
See `tests/test_postgres_content_store.py`.

**Decision: do not extract a persistence interface/repository abstraction now.**
`ContentStore`'s existing method surface *is* the persistence boundary every other module
already depends on exclusively — introducing an additional interface on top of it before a
second backend exists would be speculative. `ContentStore` should remain *the* persistence
abstraction through both the ownership (Milestone 3.2) and Postgres (Milestone 3.3)
migrations.

**This did not mean its method signatures stayed frozen — Milestone 3.2 confirmed that.**
`get_due_platform_posts`, `get_recoverable_platform_posts`, `get_reconcilable_platform_posts`,
`claim_platform_post`, `update_platform_post_if_unchanged`, `insert_video`,
`insert_slot_if_missing`, `insert_platform_post`, `insert_platform_post_if_missing`,
`find_earliest_open_slot`, and `find_earliest_open_slot_fifo` all gained an optional
`user_id: int | None = None` parameter (omitting it reproduces the exact pre-3.2 unscoped
behavior — see ADR-0007 for why optional rather than required, and why a scoped-object
wrapper like `ContentStore.for_user(user_id)` was evaluated and set aside in favor of this
simpler shape for now). `assign_slot` gained an ownership-consistency check
(`OwnershipMismatchError`) rather than a new parameter. What was preserved across this
change, exactly as anticipated, is not the exact pre-3.2 parameter lists but the *scheduling
and concurrency semantics* these methods implement (atomic claim, optimistic concurrency —
see above) and the multi-tenant execution invariant below, which every scoped call now
satisfies — proven directly by `tests/test_ownership.py`'s cross-tenant isolation tests, not
merely asserted.

`ContentStore`'s SQLite-specific internals (the two migration functions above, the PRAGMAs)
remain exactly the parts expected to be replaced outright, not adapted, regardless of how the
rest of its method surface evolves for ownership.

No code changes were made to `ContentStore` this milestone — none were required to establish
this boundary; it already existed.

## 7. Media / Storage Boundary

**Milestone 3.4 update:** implemented — see ADR-0009 and the Milestone 3.4 evaluation record.
`storage.protocol.StorageProtocol` (put/exists/delete/materialize), two real implementations
(`storage.local.LocalStorage`, `storage.supabase_storage.SupabaseStorage`), a
`storage.factory.build_storage()` selector mirroring `store_factory`'s no-silent-fallback
philosophy, and `media.media_storage` (the domain-level upload/materialize bridge, tenant-
checked) all exist and are tested against real Supabase Storage. The rest of this section is
kept as originally written (Milestone 3.1) to show what was predicted vs. what was actually
built — see the "Modules requiring adaptation" paragraph below for the as-built outcome per
module.

**Local-filesystem assumptions found:**

- `config.CONTENT_DIR`/`INCOMING_DIR`/`PROCESSED_DIR`/`FAILED_DIR` — local directories.
- `videos.canonical_media_path` / `videos.original_path` — stored as local filesystem path
  strings in SQLite.
- `media.inspection.inspect_media`/`extract_audio` — `ffprobe`/`ffmpeg` subprocess calls
  against local paths; cannot operate on a remote URI directly.
- `publishing/tiktok/publisher.py`'s `publish()` — `video_path.open("rb")`, a real local file
  handle, read in full before the upload PUT.
- `media.processing._move_file` — `shutil.move` between local directories
  (`content/incoming/` → `content/processed/`|`content/failed/`).
- `config.EMBEDDING_CACHE_DIR` — a local cache directory for `fastembed`'s downloaded model
  weights (an infrastructure/cache assumption, not a media-content one, but local-filesystem
  nonetheless — see §16 for why this is safe to defer).

**Future media contract (conceptual, per the brief — not implemented this milestone):**

```text
media record
  -> logical asset identifier / storage reference
  -> runtime obtains a readable local/temp path or stream when needed
```

Concretely: `videos.canonical_media_path` stops meaning "the" filesystem path and starts
meaning "a reference" (a storage key/URI), and a thin storage adapter resolves that reference
to a real local temp file immediately before any of the four call sites above that need actual
bytes on disk (`ffprobe`, `ffmpeg`, TikTok's `FILE_UPLOAD` PUT all fundamentally need bytes,
regardless of backend — object storage does not remove that requirement, it only moves where
the bytes permanently live between operations).

**Modules identified for adaptation in Milestone 3.1 — as-built outcome in Milestone 3.4:**

- `media/inspection.py` — **not modified.** `inspect_media`/`extract_audio` still take a plain
  local `Path`; the materialization step happens one layer up
  (`media.media_storage.materialize_canonical_media`), so these functions never needed to learn
  anything about storage.
- `media/processing.py` — **minimally extended during review, not rewritten.** The existing
  local ingestion pipeline (`content/incoming/` → `content/processed/`|`content/failed/`,
  `process_one`, `discover_videos`) is behaviorally unchanged for every existing caller — verified
  by the full pre-existing test suite passing with zero modifications. What *was* added: two
  optional hooks on `process_one` (`on_failed`/`on_assigned`, defaulting to the exact pre-3.4
  `_move_file` behavior) and a new `process_storage_backed_video` entry point that reuses
  `process_one`'s inspection/transcription/caption/scheduling logic against a temporary
  object-storage materialization instead of a permanent local file. This was a correction, not
  part of the original milestone pass — an earlier draft left `media/processing.py` fully
  untouched and left ingestion as the *only* processing path, which contradicted this document's
  own "local files are only ever temporary materializations" framing (that framing was always
  meant to describe videos that have actually been uploaded to object storage, never local CLI
  ingestion — see ADR-0009's "Media processing" section for the full correction record). Uploading
  an already-processed local video to object storage remains a separate, additive step
  (`media.media_storage.upload_canonical_media`, invoked by
  `cli/migrate_media_to_object_storage.py`) — nothing about local ingestion's own semantics
  changed.
- `publishing/tiktok/publisher.py` — **not modified**, deliberately, per the brief's own explicit
  preference ("keep TikTokPublisher's platform behavior unchanged... rather than teaching
  TikTokPublisher about Supabase/S3 directly"). `scheduling/publish_tiktok.py` (the
  application/job layer) resolves storage instead — `execute_claimed_platform_post`/
  `publish_video` gained an optional `storage` parameter and materialize the video to a temp
  path *before* ever calling `publisher.publish()`, which still only ever receives a plain
  `Path`.
- `persistence/content_store.py` — two new nullable columns (`storage_provider`, `storage_key`)
  on `videos`, added via the existing additive pattern (SQLite) and a new forward migration
  (Postgres, `0002_add_media_storage_columns.sql` — `0001` was not rewritten). Exactly as
  predicted: `canonical_media_path`/`original_path` stayed `TEXT` columns; only their *meaning*
  changed, and only for videos that have actually been migrated (`storage_provider` set) —
  every other video (all of them, before this milestone's own real-media migration ran) is
  completely unaffected.

Object storage is implemented — Supabase Storage, via its REST API. See §14/§16 and ADR-0009.

## 8. Credential / Platform-Connection Boundary

**Milestone 3.2 update:** the `user -> platform_connection` *identity/status* model described
below as conceptual is now real (`platform_connections` table, `UNIQUE(user_id, platform)`,
`external_account_id`, `status` — see ADR-0007). What remains exactly as conceptual as
before: the actual credential *secret* (TikTok's access/refresh token) still lives nowhere but
`config.TIKTOK_TOKEN_PATH`'s single local file — `platform_connections` deliberately stores no
secret material, so this section's "current single-global assumptions" list below is still
accurate for the credential itself, only now paired with a real per-user connection row that
identifies *whose* credential it conceptually is.

**Current single-global assumptions, all confirmed by direct inspection:**

- `config.TIKTOK_TOKEN_PATH` (`~/.config/content-calendar/tiktok_token.json`) — one TikTok
  account, one file, for the entire deployment. `tiktok_auth.get_access_token()` and
  `TikTokPublisher._headers()` both read this one fixed path; neither takes a
  connection/account identifier.
- `config.CALENDAR_OAUTH_TOKEN_PATH`/`CALENDAR_OAUTH_CLIENT_SECRETS_PATH` — one Google
  Calendar OAuth identity.
- `config.APP_CALENDAR_STATE_PATH` (`data/calendar_state.json`) — one dedicated Calendar per
  deployment (`calendar_manager.resolve_app_calendar`).
- `tiktok_auth.TIKTOK_REFRESH_LOCK_PATH` — one process/host-local `fcntl.flock` guarding the
  one token's refresh path; explicitly documented in the module itself as scoped to
  "single-host CLI process," not multi-host coordination.

**Future model (conceptual, per the brief):**

```text
user -> platform_connection -> encrypted credential state -> TikTok publisher/token manager
```

Concretely: the single `tiktok_token.json` file becomes one `platform_connections` row per
(user, platform) holding encrypted access/refresh tokens and expiry; `get_access_token()`/
`TikTokPublisher` take a connection identifier instead of a fixed path; the process-local
`fcntl` lock becomes a per-connection guard — the same optimistic-concurrency pattern already
proven by `update_platform_post_if_unchanged` (a DB-row-scoped compare-and-swap) is the
natural replacement, not a new mechanism. The same reasoning applies symmetrically to Google
Calendar's OAuth identity and dedicated-calendar ownership.

**What Milestone 3.2 actually built toward this:** `platform_connections(user_id, platform,
external_account_id, status)` — the identity/status half of this model — plus a one-time
bridge (`cli/backfill_ownership.py`) that reads the existing cached token file's `open_id`
(never `get_access_token()` — a local file read only, no network call, no refresh) into that
row's `external_account_id`. **Still explicitly deferred, unchanged from the original
Milestone 3.1 writing:** the credential secret itself does not move into this table, into
Postgres, or into any hosted store — `TIKTOK_TOKEN_PATH` remains the single source of truth
for the actual token, and `get_access_token()`/`TikTokPublisher` are unchanged (still read
that one fixed path, not a connection identifier). This section only maps *where* the
credential boundary will eventually sit, not where the bytes live yet — see ADR-0007's
"Current Local Credential Bridge" for the full reasoning.

See §5's multi-tenant execution invariant for the job-side requirement this credential model
exists to support: once credentials are per-`platform_connection`, every job that calls
`get_access_token()`/`TikTokPublisher` (or the Calendar OAuth equivalent) must resolve the one
connection belonging to the row/user it is currently acting on — never a different one, and
never a single shared connection standing in for all users, which is what today's single
global token file effectively is.

## 9. Configuration Boundary

Every setting in `config.py`, classified:

| Category | Examples |
|---|---|
| **Static application config** | `RETRY_BACKOFF_MINUTES`, `STATUS_CHECK_BACKOFF_SECONDS`, `PLATFORM_POST_STALE_MINUTES`, `TIKTOK_MAX_CAPTION_UTF16_UNITS`, `TIKTOK_CONTAINERS`/`TIKTOK_VIDEO_CODECS`, `EVENT_DURATION_MINUTES`, `SUPPORTED_VIDEO_EXTENSIONS`, `AUTO_ASSIGN_THRESHOLD`, `EMBEDDING_MIN_SIMILARITY`/`EMBEDDING_MIN_MARGIN`, `WHISPER_MODEL_SIZE`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE` |
| **Environment/deployment config** | `DB_PATH` (→ future `DATABASE_URL`), `TIKTOK_API_BASE`/`TIKTOK_AUTHORIZE_BASE`, `ANTHROPIC_API_KEY`, `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET` (the app's own Developer Portal registration — shared by every user's connection, not per-user), `EMBEDDING_CACHE_DIR` |
| **Per-user settings (future)** | `POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`, `ROUTING_MODE`, `CAPTION_MODE`, `CONTENT_TYPES` (pillar definitions/weights), `TARGET_PUBLISHING_PLATFORMS`, `CLASSIFIER` choice, `TIMEZONE` (deployment-level default today; per-creator later) |
| **Platform-connection state (future)** | Contents of `TIKTOK_TOKEN_PATH` (access/refresh token, expiry, `open_id`, scope), contents of `CALENDAR_OAUTH_TOKEN_PATH`, contents of `APP_CALENDAR_STATE_PATH` (`calendar_id`/`summary`) |
| **Runtime/job state** | Already correctly in the database, not `config.py`: `next_retry_at`, `next_status_check_at`, `status_check_count`, `retry_count`, `platform_posts.status`, `videos.status` — listed here only to complete the classification, not because anything needs to move |

No values were moved this milestone (guardrail) — this table exists to guide later
schema/settings-API design (Milestones 3.2+), not to change `config.py` now.

## 10. User-Ownership Impact Map

**Milestone 3.2 update:** the first three rows below are now implemented, not just
identified — see ADR-0007 for the schema and `docs/evaluations/productization/
milestone-3.2-user-auth-ownership.md` for validation evidence. The remaining rows are
unchanged from the original Milestone 3.1 mapping.

**Will eventually require `user_id`/ownership:**

- `videos`, `content_slots`, `platform_posts` (today's three core tables) — **implemented**:
  each now carries a nullable `user_id` (see ADR-0007 for why nullable, not `NOT NULL`, under
  SQLite).
- Platform connections — **implemented** as `platform_connections(user_id, platform,
  external_account_id, status)`; the credential secret itself (TikTok token state, Google
  Calendar OAuth token state) is not yet part of this table — see §8.
- Creator settings (`POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`, `ROUTING_MODE`,
  `CAPTION_MODE`, `CONTENT_TYPES`, `TARGET_PUBLISHING_PLATFORMS`).
- Calendar configuration (the dedicated-calendar identity currently in
  `APP_CALENDAR_STATE_PATH`).
- Uploaded media (once object storage is per-user-scoped — see §7).
- Captions (`videos.caption_text`/`caption_source` — already columns on `videos`, so covered
  by `videos`' own ownership; not a separate entity).

**Remain global/system-level:**

- Operational tuning defaults (`RETRY_BACKOFF_MINUTES`, `STATUS_CHECK_BACKOFF_SECONDS`,
  `PLATFORM_POST_STALE_MINUTES`) — unless a future tier explicitly wants per-user overrides,
  the default should stay system-level.
- Platform-defined constants (`TIKTOK_MAX_CAPTION_UTF16_UNITS`, `TIKTOK_CONTAINERS`/
  `TIKTOK_VIDEO_CODECS`, `SUPPORTED_VIDEO_EXTENSIONS`).
- Model/engine settings (`WHISPER_MODEL_SIZE`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`) —
  unless per-user model choice becomes an actual product feature.
- The app's own platform API registrations (`TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`,
  `TIKTOK_API_BASE`/`TIKTOK_AUTHORIZE_BASE`) — one Developer Portal app shared by every
  user's individual `platform_connection`, not duplicated per user.

No DB schema was altered by the original Milestone 3.1 writing of this section (guardrail) —
it was a forward-looking map only. Milestone 3.2 implemented the `users`/`auth_identities`/
`platform_connections` tables and the `user_id` columns above; see ADR-0007. The remaining
entities in this map (creator settings, calendar configuration, uploaded media) are still
forward-looking only — no schema change was made for them.

## 11. Target Request / Job Flows (Conceptual)

**Upload flow**
```text
client -> API -> upload/storage record (videos row + storage reference)
       -> media processing job (process_one: inspect, transcribe, caption)
       -> video becomes schedulable (slot-matched, platform_posts materialized)
```

**Schedule flow**
```text
client -> API -> schedule/cadence persisted (per-user POSTS_PER_WEEK/POSTING_DAYS/POSTING_TIME)
       -> platform_posts materialized (platform_post_materializer, on slot assignment)
```

**Publish flow**
```text
scheduler -> due-post job (worker.run_due_posts_once)
          -> claim (ContentStore.claim_platform_post, atomic)
          -> publisher (Publisher.publish -> TikTokPublisher)
          -> TikTok
          -> reconciliation if needed (reconciliation.reconcile_pending_status_checks_once)
```

**Status flow**
```text
client -> API -> DB (ContentStore reads) -> current publish state (platform_posts.status)
```

**Reconnect flow**
```text
UI -> OAuth (TikTok Login Kit / Google Calendar consent)
   -> connection credentials persisted (per-user platform_connection, §8)
   -> backend resumes eligible work (worker/reconciliation pick the connection back up)
```

These remain conceptual; no FastAPI routes or job-trigger infrastructure were built.

## 12. Future API Package Location

```text
src/content_automation/api/
  app.py
  routes/
  schemas/
  dependencies/
```

Matches the existing `src/content_automation/<package>/` convention (`media/`, `scheduling/`,
`publishing/`, `persistence/`, `calendar/`) rather than inventing a new top-level layout. No
files were scaffolded this milestone — per the brief's own preference for documentation over
speculative empty files, and because there is nothing yet for an empty `api/` package to
import or be tested against.

## 13. Service Layer: What Already Exists, What Doesn't

Milestone 3.0's package/CLI split already produced the application-service layer this phase
would otherwise need to extract — confirmed by re-reading each candidate function the brief
names as an example:

| Brief's example | Already-existing function | Status |
|---|---|---|
| upload/process video | `media.processing.process_one` (+ `discover_videos`) | Exists, plain parameters, already CLI- and (future) API/job-callable |
| schedule video | `calendar.generate_calendar.build_schedule` (pure) + `push_events` (I/O) | Exists |
| reschedule post | — | **Gap.** No dedicated "reschedule an already-assigned post" function exists today. Not built this milestone (guardrail: no new functionality) — flagged for whichever future milestone adds user-facing schedule editing. |
| connect platform | `publishing.tiktok.auth.authorize_interactive`/`exchange_code_for_token`, `calendar.calendar_manager.build_oauth_calendar_service` | Exist, but built for a single local interactive user (browser-based, localhost callback) — will need a web-redirect-flow adaptation in a later milestone, not now. |
| get queue | `ContentStore.list_slots_by_status`, `get_due_platform_posts`-style reads | Exist |

**Conclusion: no generic `services/` module was created.** Per the brief's own instruction
("do not create generic `services/` modules just for architecture aesthetics... only
recommend/extract a service if multiple future callers clearly need it"), the existing
package functions already satisfy that test — CLI and a future FastAPI/job runner already can
call the same functions directly, which was 3.0's explicit purpose. The one real gap found
(reschedule) is recorded, not filled, since filling it is new functionality outside this
milestone's scope.

## 14. Hosted Infrastructure Decision Matrix

| Category | Requirements | Constraints | Interface the code should depend on | Decision needed now? |
|---|---|---|---|---|
| API hosting | Run FastAPI, reachable by web/mobile clients | Must not block on long-running work (§4) | Standard ASGI app | No — deferred to whichever milestone builds `api/` |
| Postgres | Multi-connection concurrency, real `ALTER TABLE`, user-scoped rows | Must preserve `ContentStoreProtocol`'s contract (§6); timestamp convention decision | `PostgresContentStore` (implemented) | **Done (Milestone 3.3)** — Supabase Postgres, via the session pooler (IPv4-compatible; the direct connection host is IPv6-only). See ADR-0008. |
| Object storage | Durable, addressable by a logical reference; readable as bytes/stream on demand | Must support the "resolve reference → local temp path" pattern (§7) | `StorageProtocol` (implemented) | **Done (Milestone 3.4)** — Supabase Storage, via its REST API (private buckets, service-role key). See ADR-0009. |
| Background execution | Run the four existing one-pass functions (§5) on triggers, possibly concurrently across users | Must not require converting them to daemons/loops — they're already one-pass | A job-runner invocation contract (function in, summary out) — already satisfied by existing signatures | No — provider/framework choice deferred |
| Scheduled jobs | Trigger publishing/reconciliation/recovery on an interval per connection/user | Must respect existing backoff/staleness config (§9) | A scheduler that calls the existing one-pass functions | No — deferred |
| Secrets management | Store per-user platform credentials (§8), app-level API keys | Never expose server-only credentials client-side (global `SECURITY.md`) | Whatever the credential-storage adapter in §8 ends up being | No — deferred; do not move tokens into Postgres yet (guardrail) |
| Frontend hosting | Serve `web/` (Next.js, already deployed via `netlify.toml`) | Explicitly no shared code with the Python backend in either direction | N/A — already decided and running (Netlify) | Already decided; out of this milestone's scope |

No provider was selected purely because it is familiar (guardrail respected) — every open
row above is explicitly left open.

## 15. Migration Seams / Risk List

Ranked by urgency, all found by direct inspection (not assumed):

**Must fix before hosted (multi-tenant) launch:**
- Single global TikTok credential file and single global Google Calendar OAuth identity/
  dedicated calendar (§8) — blocks any second real user by construction, not by a missing
  feature flag.
- `config.SERVICE_ACCOUNT_FILE`'s default pointing outside this repository
  (`~/growth_agency/credentials/service-account.json`, used only by the explicit `--calendar`
  override path per `AGENTS.md`) — a hosted deployment must not depend on a path inside a
  different local repo; low urgency in practice today since this override path is
  advanced/debug-only and unused in normal operation, but must become a real secret before
  that override path is ever exposed through a hosted API.

**Milestone-specific (tied to a specific future milestone, not urgent now):**
- Local absolute media paths stored in `videos.canonical_media_path`/`original_path` (§7) —
  Milestone 3.4 (object storage). Not touched by 3.3 — the SQLite→Postgres migration copied
  these path strings verbatim; their *meaning* still needs 3.4's object-storage boundary.
- `tiktok_auth._refresh_lock()`'s `fcntl.flock`-based process/host-local advisory lock — fine
  for a single-host CLI, will not correctly serialize refreshes across multiple hosted worker
  processes/containers. Becomes a per-connection DB-row lock (same optimistic-concurrency
  pattern as `update_platform_post_if_unchanged`) once hosted background workers exist —
  whichever milestone introduces those. Still open; Postgres migration alone doesn't touch
  credential refresh locking.

**Resolved by Milestone 3.3** (listed here for continuity with the original 3.1 risk ranking):
- SQLite's naive-local-time (`scheduled_at`) vs. aware-UTC (`updated_at`/`published_at`/
  `next_status_check_at`) timestamp convention split — preserved exactly under Postgres
  (`TIMESTAMP` for naive-local columns, `TIMESTAMPTZ` for aware-UTC ones, session time zone
  forced to UTC at connect time), not normalized. See ADR-0008 "Timestamps".
- SQLite's single-writer/file-lock concurrency model vs. Postgres's real concurrent-connection
  model — verified directly under real concurrent Postgres connections (five real threads
  racing to claim one row; a real stale-CAS proof), not assumed. See
  `tests/test_postgres_content_store.py`.

**Safe to defer:**
- Local model/cache directories (`EMBEDDING_CACHE_DIR`, faster-whisper's own cache) — public
  model weights, not secrets; fine to re-download per host/container, or share a volume later,
  either way with no correctness risk.
- CLI process invocation assumptions (`cli/*.py` expecting to run from repo root inside the
  project venv) — largely already resolved by Milestone 3.0's thin-CLI/package split; a
  hosted FastAPI route or job runner bypasses the CLI entirely.

## 16. Deferred Provider Decisions

As of Milestone 3.1: Postgres provider, object storage provider, background-execution/queue
technology, scheduled-job infrastructure, secrets manager, API hosting provider — none
selected, per guardrails.

**Milestone 3.3 update:** Postgres provider is now decided — Supabase Postgres, via its
session connection pooler (see ADR-0008 "Provider"). Object storage provider,
background-execution/queue technology, scheduled-job infrastructure, secrets manager, and API
hosting provider remain deferred exactly as before.

**Milestone 3.4 update:** Object storage provider is now decided — Supabase Storage, via its
REST API (see ADR-0009 "Provider"). Background-execution/queue technology, scheduled-job
infrastructure, secrets manager, and API hosting provider remain deferred. See §14 for the
current matrix.

## 17. Summary

This milestone found that Milestone 3.0's package refactor already produced most of the
hosted-architecture boundary this document was asked to define: a clean persistence boundary
(`ContentStore`), a clean publishing boundary (`Publisher`), four already-one-pass background
functions, and a thin CLI with no logic of its own. What remained genuinely undefined — the
API/background split, the media/storage contract, the credential/user-ownership model, the
configuration classification, the migration risk ranking, and the multi-tenant execution
invariant every future job design must satisfy (§5) — is now documented above.
`ContentStore` remains the persistence abstraction going forward; its method signatures were
explicitly not frozen.

**Milestone 3.2 addendum:** the user/ownership model predicted above is now real —
`users`/`auth_identities`/`platform_connections` tables, nullable `user_id` on
`videos`/`content_slots`/`platform_posts`, an `OwnershipMismatchError` invariant on
`assign_slot`, and optional tenant scoping proven end-to-end on all four background job
functions (§5). See ADR-0007 and the Milestone 3.2 evaluation record for the full
implementation and validation evidence.

**Milestone 3.3 addendum:** Postgres persistence is now real — `PostgresContentStore`, tested
against actual Supabase Postgres (real concurrent-connection atomic claim, real stale-CAS
rejection, real cross-tenant isolation, a real SQLite→Postgres data migration preserving every
id/relationship/publishing-state field, all backed by 20 new passing tests). `user_id` is
`NOT NULL` under Postgres, resolving the nullable-under-SQLite compromise Milestone 3.2 made
deliberately and only for SQLite (ADR-0007/ADR-0008).

**Milestone 3.4 addendum:** object storage is now real — `SupabaseStorage`, tested against
actual Supabase Storage (put/exists/materialize/delete round trips, a real 5MB streamed
transfer, tenant-isolation proofs at the application boundary, a full real-Postgres +
real-object-storage + `FakePublisher` end-to-end pipeline covering both processing and
publishing). `videos.storage_provider`/`storage_key` are nullable under both backends (unlike
`user_id` — no real row had a value to backfill before this migration existed, so there was
nothing to make `NOT NULL` yet). `scheduling/publish_tiktok.py`'s
`execute_claimed_platform_post`/`publish_video`, and (added during review)
`media.processing.process_one`/the new `process_storage_backed_video`, all gained optional
storage-related parameters/entry points consulted only for a video that has actually been
migrated — the pre-3.4 direct-local-path behavior is unchanged for every other video, verified
by the full 696-test suite passing with zero existing tests modified. The five real production
videos were migrated (uploaded, SHA-256-verified, local originals retained). Real
auth-provider integration and hosted credential storage remain exactly as future as they were
at the original Milestone 3.1 writing of this document.
