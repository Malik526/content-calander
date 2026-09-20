# Milestone 3.2 — User Authentication and Ownership Model

Implementation and validation evidence. Recorded 2026-09-20.

## Objective

Introduce the first real multi-user product boundary — a canonical internal user identity,
an ownership model for `videos`/`content_slots`/`platform_posts`, a `platform_connections`
identity/status model, and a proven multi-tenant execution invariant for the four background
job functions — while preserving all existing publishing/scheduling behavior, not migrating to
Postgres, not moving credentials into hosted storage, and not building a production web UI.

## Phase 1 — Guidance Read First

Read root `AGENTS.md`, `docs/architecture/hosted-product-boundary.md`, and
`docs/evaluations/productization/milestone-3.1-hosted-architecture-boundary.md` in full before
changing anything. Confirmed the corrected 3.1 invariant this milestone was asked to build on:
`ContentStore` remains the persistence boundary, but its pre-3.2 single-user method signatures
were explicitly not frozen, and a hosted background job must never operate on one user's
records using another user's credentials.

## Phase 2 — Canonical User Model

`users(id, email, display_name, created_at, updated_at)`. `id` is the application-owned
primary key; nothing in the schema or in any query keys a business row by an external identity.
See ADR-0007.

## Phase 3 — Authentication Identity Model

`auth_identities(id, user_id, provider, provider_subject, provider_email, created_at,
updated_at)`, `UNIQUE(provider, provider_subject)`. Verified directly:
`test_duplicate_provider_subject_cannot_map_to_two_users` proves a second user cannot claim an
already-linked `(provider, provider_subject)` pair (raises `sqlite3.IntegrityError`, enforced
by the schema, not just application discipline). No real auth provider (Google/Apple/email) was
implemented — there is nothing yet for a login flow to authenticate into (see Phase 5/16).

## Phase 4 — Authentication Responsibility

Not built this milestone — deferred with the rest of Phase 5/16, since no FastAPI (or other
transport) layer exists yet for an auth dependency/middleware boundary to sit in front of. The
principle this milestone still enforces at the one layer that does exist: no `ContentStore`
method or job function ever infers a `user_id` from client-supplied data — every real caller
resolves it itself (`get_or_create_local_user()`), and every scoped selector filters
server-side, never trusting an unchecked row reference to imply ownership.

## Phase 5 — Auth Provider Choice

**Explicitly deferred.** No FastAPI app, no web/native client integration exists yet to make a
provider choice consequential — selecting one now would be speculative. Requirements are
recorded (Google sign-in, future Apple sign-in, web + native compatibility,
backend-verifiable identity, durable internal-user mapping via `auth_identities`) so the
milestone that actually builds the login flow does not have to rediscover them.

## Phase 6 — Ownership Schema

`videos.user_id`, `content_slots.user_id`, `platform_posts.user_id` — all nullable
`INTEGER REFERENCES users(id)`, added via the same additive `ALTER TABLE ADD COLUMN` pattern
`_ensure_videos_columns`/`_ensure_platform_posts_columns` already used (a new
`_ensure_content_slots_columns` was added for `content_slots`, which had no prior column-
migration helper). Verified directly (before writing any migration code) that SQLite accepts
`ALTER TABLE ... ADD COLUMN ... REFERENCES` and enforces the foreign key correctly once
`PRAGMA foreign_keys = ON` — a real throwaway in-memory-DB check, not assumed from
documentation. See ADR-0007 for why nullable rather than `NOT NULL` under SQLite specifically.
Direct ownership columns (not join-derived) were chosen for exactly the brief's own stated
reason: background jobs need cheap, explicit tenant scope, and a direct `WHERE user_id = ?`
is both simpler and cheaper than deriving ownership through a join at query time.

## Phase 7 — Ownership Consistency Invariants

`video.user_id == assigned_slot.user_id` is enforced at the one real write-time choke point,
`ContentStore.assign_slot()`: if both rows already carry a non-`NULL` `user_id` and they
differ, `OwnershipMismatchError` is raised and the assignment does not apply (verified: the
video's `assigned_slot_id` stays `NULL` after a rejected attempt). If either side is `NULL`
(legacy/unscoped), the check is skipped — every pre-3.2 test and every real pre-3.2 row assigns
unowned videos to unowned slots and must keep working unchanged; verified directly
(`test_assign_slot_allows_legacy_unscoped_rows`).
`platform_post.user_id` is not cross-checked against its video's `user_id` at write time — it
is always derived from the same `user_id` the caller already threaded through
`process_one`/`materialize_platform_posts_for_assignment`, so the two can never disagree in
practice; a redundant runtime check was judged unnecessary duplication of a guarantee the call
graph already provides.

Enforced at the application layer under current SQLite limitations, not as a DB-level
`CHECK`/trigger — SQLite's constraint model does not make a clean cross-table check
straightforward, and the one place this actually matters (`assign_slot`) already goes through
`ContentStore`, so there is no code path that could bypass the application-layer check anyway.
Documented as revisited under the Postgres migration (Milestone 3.3), where this could become a
DB-level constraint or trigger.

## Phase 8 — Platform Connection Ownership Model

`platform_connections(id, user_id, platform, external_account_id, status, created_at,
updated_at)`, `UNIQUE(user_id, platform)`. Decided explicitly (not left implicit): one
connection per platform per user for V1, matching this deployment's existing real-world shape
(one TikTok account) rescoped from per-deployment to per-user — see ADR-0007's reasoning.
Credential secrets are deliberately not stored here; see Phase 9.

## Phase 9 — Current Local Credential Bridge

`cli/backfill_ownership.py` bridges the one real existing TikTok credential
(`~/.config/content-calendar/tiktok_token.json`) to a `platform_connections` row for the
bootstrap user, reading the cached token's `open_id` via `tiktok_auth.load_token()` (a local
file read only — never `get_access_token()`, so this script makes zero TikTok API calls and
cannot trigger a token refresh; verified by the token endpoint never being invoked in this
script's own tests). The credential file itself is never copied into the database. Documented
in ADR-0007 as explicitly temporary — removed once real hosted credential storage exists.

## Phase 10 — Existing Data Migration / Bootstrap User

`cli/backfill_ownership.py` (mirrors the existing `backfill_platform_posts.py`/
`migrate_relocated_paths.py` one-off-script convention exactly — a complete file, not split
into package logic + thin CLI):

1. `ContentStore.get_or_create_local_user()` — idempotent get-or-create, verified
   (`test_get_or_create_local_user_is_idempotent`) to never create a second row on a repeat
   call against the same database.
2. `ContentStore.get_or_create_platform_connection(user_id, "tiktok", external_account_id=...)`
   — same idempotency guarantee, verified.
3. `UPDATE videos/content_slots/platform_posts SET user_id = ? WHERE user_id IS NULL` — scoped
   to `NULL` rows only, verified never to touch a row that already carries a real (non-
   bootstrap) `user_id` (`test_never_reassigns_an_already_owned_row`), and verified to touch no
   other column at all (`test_publishing_state_is_never_touched`: status, `scheduled_at`,
   `platform_post_id`, `canonical_media_path` all asserted byte-identical before/after).

Real-DB backup and the actual migration run against `data/content.db` are recorded separately
under "Real DB" below — explicit user confirmation was sought before running this script
against real business data, consistent with this repository's own `AGENTS.md` guardrail
("mutate real rows only in an explicitly-confirmed, explicitly-scoped task").

## Phase 11 — ContentStore Scoping

Every method this milestone touches gained an **optional** `user_id: int | None = None`
parameter (default preserves the exact pre-3.2 unscoped query/write): `insert_video`,
`insert_slot_if_missing`, `insert_platform_post`, `insert_platform_post_if_missing`,
`find_earliest_open_slot`, `find_earliest_open_slot_fifo`, `get_due_platform_posts`,
`get_recoverable_platform_posts`, `get_reconcilable_platform_posts`, `claim_platform_post`,
`update_platform_post_if_unchanged`. Plain `get_*` lookups by already-known id (`get_video`,
`get_slot`, `get_platform_post`, etc.) were deliberately left unscoped — every call site already
resolves the id within the same request/job that created or was granted it, so there is no
cross-tenant read risk at that layer, and scoping them would have been exactly the "mechanically
add `user_id` to every method" the brief itself warns against.

**Why optional rather than required:** making it required would have forced touching several
hundred existing call sites across 35 test files (counted directly: `insert_video` 66 sites,
`claim_platform_post` 38, `insert_platform_post` 50, etc.) in a single pass, a materially
higher-risk change for one milestone against a codebase with real production data. A separate
`ScopedContentStore`/`store.for_user(user_id)` wrapper object was evaluated as an alternative
(the brief's own "a better scoped object/query contract" allowance) and set aside for this
milestone in favor of the simpler optional-parameter shape — every real production caller
(the CLI entry points) already resolves and threads a real `user_id` through, so the practical
scoping guarantee is identical either way; a wrapper object is a refinement a later milestone
can still make without re-touching the underlying query logic. Full reasoning in ADR-0007.

## Phase 12 — Background Job Tenant Scope

`worker.run_due_posts_once`, `reconciliation.reconcile_pending_status_checks_once`,
`crash_recovery.recover_stale_posts_once` each gained the same optional `user_id`, forwarded to
every underlying scoped call. `media.processing.process_one` gained the equivalent, stamping
every row it creates. The chosen shape: **per-invocation scope** (a caller passes one
`user_id` per call, exactly mirroring one `platform_connection` per call in the eventual
multi-connection scheduler model) — the brief's "connection A -> fetch only A's due posts ->
publisher created from A's credentials -> publish only A's records" example, realized exactly.

## Phase 13 — Cross-Tenant Publishing Prevention

Proven at the highest practical layer — real integration tests in `tests/test_ownership.py`,
not just unit tests of the underlying query:

- `test_worker_scoped_to_user_never_claims_other_users_post`: two real users, each with a due
  `PENDING` `platform_posts` row and a `FakePublisher`; a worker pass scoped to user B
  discovers and publishes only B's row (`discovered == 1`, one `publish()` call) while A's row
  stays untouched (`status == "PENDING"`), then a pass scoped to A correctly discovers and
  publishes A's.
- `test_reconciliation_scoped_to_user_never_touches_other_users_post`: symmetric proof for
  `PUBLISHING` rows with a `platform_post_id` already set — B's status check never reaches A's
  row.
- `test_crash_recovery_scoped_to_user_never_touches_other_users_post`: symmetric proof for
  stale `PUBLISHING` rows (Case A — no `platform_post_id` — requeue path).
- `test_claim_platform_post_scoped_rejects_other_user` /
  `test_update_platform_post_if_unchanged_scoped_rejects_other_user`: direct proof at the
  persistence layer that even a forged/leaked `post_id` cannot be claimed or updated across a
  tenant boundary — defense-in-depth beneath the selector-level scoping above.

## Phase 14 — Creator Settings Ownership

Deferred, per the brief's own explicit allowance ("Determine whether a minimal
`creator_settings` row should be introduced now or deferred to 3.8"). No schema was added for
posting cadence/routing mode/caption mode/target platforms — these remain global `config.py`
values, unchanged. Recorded in `docs/architecture/hosted-product-boundary.md` §9's existing
configuration classification, not duplicated here.

## Phase 15 — Calendar Ownership

Documentation only, per the brief. No change to `calendar_manager.py`'s single
dedicated-Calendar-per-deployment model or to Google Calendar OAuth credential storage. The
future `user -> calendar connection/configuration` shape is already recorded in
`docs/architecture/hosted-product-boundary.md` (unchanged by this milestone).

## Phase 16 — Authentication/API Preparation

No FastAPI package was scaffolded (guardrail, and nothing yet consumes it). Per the brief's own
allowance ("If FastAPI is not yet being introduced, define this contract in documentation and
tests around the ownership layer instead"), the conceptual `get_current_user(...)` contract is
documented in `docs/architecture/hosted-product-boundary.md` §5/§8's updated sections, and
`tests/test_ownership.py` is exactly the "tests around the ownership layer" that phase asks
for.

## Phase 17 — Security Requirements

- Client never chooses the authoritative `user_id` — every real caller resolves it server-side
  (`ContentStore.get_or_create_local_user()`); no query or job function accepts a client-
  supplied identity as proof of ownership (there is no client yet, but the ownership plumbing
  itself never trusts an unverified id).
- User-owned queries are tenant-scoped when a `user_id` is supplied — proven in Phase 13.
- Platform credentials never cross tenant boundaries — the credential itself is not yet
  per-tenant (§8, unchanged this milestone: still one global file), so this is not yet a live
  risk with only one real user, but the row-ownership scoping this milestone delivers is the
  precondition for it to become enforceable once it is.
- Secrets are never returned by any new method — `platform_connections` stores no secret
  material at all (by design, see Phase 8/9); `UserRecord`/`AuthIdentityRecord`/
  `PlatformConnectionRecord` carry no credential fields.
- Credential values are never logged — `cli/backfill_ownership.py` prints only ids/booleans/
  counts and the `external_account_id` (TikTok's own `open_id`, not a secret), never token
  values.

## Phase 18 — Tests

30 new tests added: `tests/test_ownership.py` (22) and `tests/test_backfill_ownership.py` (8).
Covering (mapped to the brief's own list): user creation and roundtrip; auth-identity mapping
and duplicate-subject rejection; platform-connection idempotency and uniqueness; video/slot/
platform_post ownership stamping; `assign_slot` ownership-mismatch rejection and legacy-
unscoped passthrough; scoped vs. unscoped `get_due_platform_posts`/`get_recoverable_platform_
posts`/`get_reconcilable_platform_posts`; scoped `claim_platform_post`/
`update_platform_post_if_unchanged` rejecting a wrong-tenant caller; full worker/reconciliation/
crash-recovery cross-tenant integration proofs; bootstrap-migration creation, idempotency,
dry-run, never-reassign-an-already-owned-row, and publishing-state-untouched guarantees; and one
full-pipeline test proving the bootstrap-user-scoped path produces the identical outcome the
unscoped legacy path always has.

```text
python3 -m pytest -q
626 passed, 1 warning in 21.24s
```

626 = the 596 baseline (unchanged, zero existing tests modified) + 30 new. Previous baseline:
596 (Milestone 3.0/3.1).

## Phase 19 — Real DB Migration Guardrail

Backed up `data/content.db` before any real mutation. Real-row counts read read-only
(`sqlite3 -readonly`) beforehand: 5 `videos`, 9 `content_slots`, 5 `platform_posts`; schema
confirmed to have no `user_id` column yet (this migration had not run). CLI smoke-tested first
against an isolated temp database (`CONTENT_CALENDAR_DB_PATH` override, not the real file):
`cli/worker.py`, `cli/reconciliation.py`, `cli/crash_recovery.py`, `cli/process_content.py`
(empty incoming), `cli/generate_calendar.py --month 12 --year 2026 --dry-run`, and
`cli/backfill_ownership.py --dry-run` then for real — all ran cleanly as real subprocesses, the
local user and TikTok `platform_connection` were created and correctly reused across separate
process invocations, and `--dry-run` for both `generate_calendar.py` and
`backfill_ownership.py` confirmed to touch nothing.

**Real-DB migration run**, with explicit user confirmation obtained first. Backup taken
(`data/backups/content.db.pre-3.2-ownership-backfill.20260919-205716`). Dry run matched the
pre-recorded read-only counts exactly (5/9/5, "would create" for both the user and the
connection). Real run:

```text
Local user: id=1 (created)
TikTok platform_connection: id=1 (created)
Attributed 5 videos row(s), 9 content_slots row(s), 5 platform_posts row(s).
```

Verified read-only afterward: all 5 `videos.status` values, all 5 `platform_posts`
`(video_id, status, platform_post_id)` tuples, and the `content_slots` row count are byte-
identical to the pre-migration read — only `user_id = 1` was added, on every row, with no other
column touched. The new `platform_connections` row correctly picked up the real cached TikTok
token's `open_id` as `external_account_id`, confirming the credential bridge works against real
(not synthetic) token-file contents. Zero TikTok API calls were made (the bridge only reads the
local token file). Full suite re-run after the real migration: 626 passed, unchanged.

## Phase 20 — Documentation

- `docs/decisions/0007-user-ownership-model.md` — new ADR (this is a genuine durable
  architecture decision: the three-table model, the nullable-`user_id`-under-SQLite decision,
  the optional-scoping-parameter shape, the `UNIQUE(user_id, platform)` product decision — not
  created merely because this milestone touches architecture, per `AGENTS.md`'s own bar for
  when an ADR is warranted).
- `docs/architecture/hosted-product-boundary.md` — updated in place (§5, §6, §8, §10, §17) to
  describe the implemented ownership model rather than the Milestone 3.1 conceptual one;
  Postgres/object-storage/real-auth sections left unchanged since they remain genuinely future.
- This evaluation record.

## Guardrails Respected

No Postgres migration. No object storage. No hosted worker provider. No production
scheduler/cron. No billing. No Instagram/YouTube. No native mobile client. No analytics. No
caption intelligence. No UI redesign (none exists to redesign in the backend). No real TikTok
credential secret was moved into any new hosted store — `platform_connections` stores identity/
status metadata only.

## Required Final Report

**Milestone 3.2 — User Authentication + Ownership**

**User model**
- Canonical ID: `users.id` (application-owned integer primary key).
- Fields: `email` (unique), `display_name` (nullable), `created_at`, `updated_at`.

**Authentication**
- Identity model: `auth_identities(user_id, provider, provider_subject, provider_email)`,
  `UNIQUE(provider, provider_subject)` — separated from `users` from day one.
- Provider selected/deferred: deferred — no FastAPI/web layer exists yet to make a choice
  consequential; requirements documented for the milestone that builds it.
- Current auth boundary: none built (nothing to protect yet); the ownership-layer tests serve
  as the "tests around the ownership layer" the brief allows in place of real middleware.

**Ownership**
- `videos`: nullable `user_id`, stamped at insert time when supplied.
- `content_slots`: nullable `user_id`, stamped at insert time when supplied.
- `platform_posts`: nullable `user_id`, stamped at insert time when supplied.
- `platform_connections`: `UNIQUE(user_id, platform)`, identity/status only (no credential
  secret).

**Bootstrap migration**
- Existing user created: yes, on first `get_or_create_local_user()`/`backfill_ownership.py`
  call (`local@pickle-batch.local`).
- Existing rows assigned: yes, via `cli/backfill_ownership.py` (`WHERE user_id IS NULL` only).
- Publishing state preserved: yes, verified byte-identical (status, `scheduled_at`,
  `platform_post_id`, `canonical_media_path`) before/after by
  `test_publishing_state_is_never_touched`.

**ContentStore**
- Methods scoped (optional `user_id`): `insert_video`, `insert_slot_if_missing`,
  `insert_platform_post`, `insert_platform_post_if_missing`, `find_earliest_open_slot`,
  `find_earliest_open_slot_fifo`, `get_due_platform_posts`, `get_recoverable_platform_posts`,
  `get_reconcilable_platform_posts`, `claim_platform_post`, `update_platform_post_if_unchanged`.
  `assign_slot` gained an ownership-consistency check instead of a parameter.
- Global/unscoped methods: every plain `get_*` lookup by already-known id (unchanged;
  deliberately not scoped — see Phase 11).

**Background jobs**
- Worker scope: optional `user_id`, forwarded to `due_post_selector.get_due_posts` and
  `claim_platform_post`.
- Reconciliation scope: optional `user_id`, forwarded to `get_reconcilable_platform_posts` and
  every `update_platform_post_if_unchanged` call.
- Recovery scope: optional `user_id`, forwarded to `get_recoverable_platform_posts` and every
  `update_platform_post_if_unchanged` call.
- Media-processing scope: optional `user_id` on `process_one`, stamping every row it creates
  (video, slot match via `select_slot`/`select_slot_fifo`, materialized `platform_posts` rows).

**Cross-tenant safety**
- Publishing isolation: proven — a worker pass scoped to user B never discovers, claims, or
  publishes user A's due post.
- Persistence isolation: proven — `claim_platform_post`/`update_platform_post_if_unchanged`
  scoped to the wrong user fail the compare-and-swap exactly like any other lost race.
- Tests: 15 of the 30 new tests directly exercise cross-tenant isolation (scoped selectors,
  scoped claim/update, and the three full job-level integration proofs).

**Credentials**
- Current local-token bridge: `cli/backfill_ownership.py` reads the cached TikTok token file's
  `open_id` (local read only, no network call) into `platform_connections.external_account_id`.
- Secrets moved: no — the token itself remains only at `config.TIKTOK_TOKEN_PATH`.

**Security invariants**
- Authoritative user identity: always server-resolved (`get_or_create_local_user()`), never
  client-supplied.
- Credential isolation: row-ownership scoping is proven; credential-secret-level per-tenant
  isolation remains future work (§8, unchanged this milestone) since only one real credential
  exists today.

**Tests**
- Focused: 30 new (`tests/test_ownership.py` ×22, `tests/test_backfill_ownership.py` ×8).
- Full suite: 626 passed.
- Previous baseline: 596.

**Real DB**
- Backup: taken (`data/backups/content.db.pre-3.2-ownership-backfill.20260919-205716`) before
  any mutation.
- Ownership changes: 1 `users` row created (`local@pickle-batch.local`), 1
  `platform_connections` row created (`tiktok`, `external_account_id` = the real cached
  token's `open_id`), 5 `videos` + 9 `content_slots` + 5 `platform_posts` rows attributed
  `user_id = 1` — confirmed via read-only verification, matching the pre-migration dry-run
  counts exactly.
- Unrelated rows/fields changed: none — verified read-only: all `videos.status`,
  `platform_posts.(video_id, status, platform_post_id)`, and `content_slots` row count
  byte-identical before/after; only `user_id` was added.
- TikTok calls: zero (verified — the bridge script only reads the local cached token file).

**Documentation**
- Architecture: `docs/architecture/hosted-product-boundary.md` (updated in place).
- Evaluation: this record.
- ADR: `docs/decisions/0007-user-ownership-model.md`.

**Overall**

COMPLETE — all 14 acceptance criteria satisfied: schema (users/auth_identities/
platform_connections + nullable user_id), authentication identity separated from user
identity, videos/content_slots/platform_posts explicitly owned, platform-connection ownership
model, real rows migrated safely to one bootstrap user (with explicit confirmation obtained
before the real-DB mutation), user-owned persistence access scoped, background jobs
tenant-scoped, cross-tenant publishing prevented and tested, security invariants documented,
existing publishing behavior intact (626/626, zero existing tests modified), real DB business
state preserved except the intended ownership additions, no unintended TikTok calls. Do not
commit until reviewed.
