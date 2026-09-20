# ADR-0007: User Ownership Model

## Status

Accepted

## Context

Through Milestone 3.1, Content Automation / Pickle Batch is structurally single-tenant: one SQLite
database, one filesystem, one TikTok credential file, one Google Calendar OAuth identity, one
dedicated Calendar. `docs/architecture/hosted-product-boundary.md` (Milestone 3.1) mapped which
concepts would eventually need `user_id`/ownership (videos, content_slots, platform_posts, platform
connections, creator settings, calendar configuration) but made no schema change — that milestone was
documentation-only by design.

Milestone 3.2 is the first milestone to actually introduce ownership. It must do so without migrating
to Postgres (Milestone 3.3), without moving TikTok credentials into hosted storage, and without
building a production web UI or a real authentication provider integration — none of those have a
reason to exist yet (no FastAPI app exists; there is nothing for a login flow to protect). The goal is
narrower and deliberately so: make the *data model* tenant-aware, and make the four background job
functions capable of running within an explicit tenant scope, so Milestone 3.3 migrates an
already-ownership-aware schema instead of solving persistence migration and multi-user isolation at
the same time.

## Decision

### Three new tables, not columns bolted onto existing ones

`users`, `auth_identities`, `platform_connections` — kept separate from each other and from
`videos`/`content_slots`/`platform_posts`, mirroring this codebase's existing preference for one
table per distinct concern (see `content_store.py`'s own docstring: "`videos` is canonical
content/media metadata, `content_slots` is scheduling assignment, `platform_posts` is external
publishing state/result").

- **`users`** — the canonical internal Pickle Batch identity, keyed by an application-owned integer
  primary key. Never keyed by an external provider's ID (Google `sub`, Apple identifier, TikTok
  `open_id`) — those change providers, and this codebase already has one clean historical precedent
  for why that separation matters (Google Calendar's own OAuth identity is already independent of any
  business-table key).
- **`auth_identities`** — one row per (external auth provider, external subject) a user has signed in
  with, `UNIQUE(provider, provider_subject)`. Separated from `users` from day one, even though only
  one provider will realistically ever be linked before a real auth flow exists, specifically so a
  future login-method addition (Google → Apple → email/magic-link) is a new row, not a schema change.
- **`platform_connections`** — one row per (user, publishing platform) identity/status metadata,
  `UNIQUE(user_id, platform)`. **Deliberately does not store credential secrets.** The real TikTok
  access/refresh token stays exactly where it already lives — a single local file at
  `config.TIKTOK_TOKEN_PATH` — for the duration of this milestone; moving it into this table, or into
  Postgres, is explicitly out of scope (see "Current Local Credential Bridge" below and the Milestone
  3.2 evaluation record). `UNIQUE(user_id, platform)`: one connection per platform per user for V1 —
  the same "one TikTok account" shape this deployment already has today, rescoped from
  per-deployment to per-user. A future product tier needing multiple accounts per platform per user is
  a new milestone's schema change, not something this decision tries to anticipate.

### `user_id` is nullable at the SQL level, not `NOT NULL`

`videos.user_id`, `content_slots.user_id`, and `platform_posts.user_id` are added as nullable
`INTEGER REFERENCES users(id)` columns via the same additive `ALTER TABLE ADD COLUMN` pattern this
codebase already uses (`_ensure_videos_columns`/`_ensure_platform_posts_columns`), not the
rename/rebuild pattern used elsewhere in `content_store.py` for a genuine constraint change.

SQLite cannot add a `NOT NULL` column with a foreign key to an already-populated table in one step
without that rebuild — and this table holds real production rows (5 videos, 9 content_slots, 5
platform_posts as of this milestone). Forcing a full rebuild-with-backfill of three tables in a single
transaction, against real business data, for a column whose actual invariant ("every row has exactly
one owner") is really an *application*-level guarantee going forward, was judged not worth the added
risk this milestone. The ownership invariant is instead enforced where it actually matters: every real
write path stamps `user_id` explicitly (every CLI entry point resolves the local user first), and
`ContentStore.assign_slot()` cross-checks two already-written rows' ownership before letting an
assignment proceed. **This is revisited under the Postgres migration (Milestone 3.3)**, where adding a
`NOT NULL` constraint (or Postgres row-level security) after a one-time backfill is a strictly smaller,
safer operation than it is under SQLite today.

### `ContentStore`'s existing method signatures are extended, not replaced

Every method this milestone touches (`insert_video`, `insert_slot_if_missing`, `insert_platform_post`,
`insert_platform_post_if_missing`, `find_earliest_open_slot`, `find_earliest_open_slot_fifo`,
`get_due_platform_posts`, `get_recoverable_platform_posts`, `get_reconcilable_platform_posts`,
`claim_platform_post`, `update_platform_post_if_unchanged`) gains an **optional** `user_id: int | None
= None` parameter. Omitting it reproduces the exact pre-3.2 unscoped query/write — every one of the
596 tests that existed before this milestone passes completely unchanged.

This was a deliberate choice over making `user_id` required everywhere (which would have forced
updating several hundred existing call sites across 35 test files in a single pass) and over a fully
separate `ScopedContentStore` wrapper object (which `docs/architecture/hosted-product-boundary.md`'s
own §6 left open as "a better scoped object/query contract" — evaluated and set aside for this
milestone specifically because the optional-parameter approach already gives every real production
caller a scope-explicit path without the added surface of a second store type to keep in sync). The
real enforcement point is not "the parameter exists" but where it is actually supplied: every CLI entry
point (`cli/worker.py`, `cli/reconciliation.py`, `cli/crash_recovery.py`, `cli/process_content.py`,
`cli/generate_calendar.py`) resolves `ContentStore.get_or_create_local_user()` and threads its id
through every call that accepts one — the unscoped path is now the *fallback available to tests and
future intentionally-global tooling*, not what real production traffic exercises.

Making `user_id` required on these methods, and on the job-orchestration functions below, is
explicitly deferred until a real multi-connection scheduler exists that can always supply it — see the
Milestone 3.2 evaluation record's "Deferred" section.

### Background jobs take an optional `user_id`, enforced by real cross-tenant tests

`worker.run_due_posts_once`, `reconciliation.reconcile_pending_status_checks_once`, and
`crash_recovery.recover_stale_posts_once` each gained the same optional `user_id` parameter, forwarded
to their underlying selector/claim/update calls. This is the concrete mechanism behind the multi-tenant
execution invariant `docs/architecture/hosted-product-boundary.md` §5 states ("a hosted background job
must never operate on one user's records using another user's credentials") — proven, not just
asserted, by `tests/test_ownership.py`'s integration tests: two real users, two real videos, a worker
pass scoped to user B that never discovers or claims user A's due post, and the symmetric proof for
reconciliation and crash recovery.

### `get_or_create_local_user()` / `get_or_create_platform_connection()`: resolve-or-create, not a
### hard-fail bootstrap requirement

Modeled directly on this codebase's own existing precedent, `calendar_manager.resolve_app_calendar`'s
"reuse via persisted state, create if missing" shape, applied to the local user identity instead of
the dedicated Calendar. Every CLI entry point can run without a separate manual bootstrap step. A
one-time `cli/backfill_ownership.py` script (matching the existing `backfill_platform_posts.py`/
`migrate_relocated_paths.py` one-off-script convention — a complete file, not split into package logic
+ thin CLI, since it is a rarely-invoked operational tool, not part of the ongoing runtime surface) is
still needed for one thing `get_or_create_local_user()` cannot do on its own: retroactively attributing
*pre-existing* rows (created before this migration, with `user_id IS NULL`) to that bootstrap user, and
bridging the existing local TikTok credential file to a `platform_connections` row.

### `assign_slot()` cross-checks ownership; other reads stay unscoped

`ContentStore.assign_slot()` now raises `OwnershipMismatchError` if the video and the slot it is being
assigned to both carry a non-`NULL` `user_id` and those values differ — the one real write-time choke
point for the invariant "a video's assigned slot shares its owner" — while leaving every plain `get_*`
lookup (`get_video`, `get_slot`, `get_platform_post`, etc.) unscoped. Those getters are always called
with an ID the calling code already resolved within the same request/job, so there is no cross-tenant
read risk to close at that layer; adding scoping there would be exactly the "mechanically add
`user_id` to every method" this milestone's own brief warns against, with no corresponding safety
benefit.

### Current Local Credential Bridge

The one real TikTok credential this deployment has ever had (`~/.config/content-calendar/
tiktok_token.json`) is bridged to the bootstrap user's `platform_connections` row by
`cli/backfill_ownership.py`, which reads the cached token file's `open_id` (a local file read via
`tiktok_auth.load_token()` — never `get_access_token()`, so this never triggers a token refresh or any
network call) and stores it as `external_account_id`. The token itself is never copied into the
database; it stays exactly where `config.TIKTOK_TOKEN_PATH` already points. This bridge is explicitly
temporary: once real hosted credential storage exists (a later milestone, not 3.3), the connection
model this ADR establishes is what that storage attaches to — the identity/status row, not the secret.

## Consequences

- Every future ownership-scoped query, wherever it is added, should follow the same optional-`user_id`
  shape established here unless a specific caller has a concrete reason to require it — consistency
  matters more than any individual method's theoretical purity.
- The Postgres migration (Milestone 3.3) inherits an already-tenant-aware schema and must decide, not
  invent from scratch, how `user_id` becomes enforceable at the database level (a real `NOT NULL`
  constraint after a one-time backfill, and/or Postgres row-level security).
- Any future scheduler/job-runner that invokes `worker.run_due_posts_once`/
  `reconciliation.reconcile_pending_status_checks_once`/`crash_recovery.recover_stale_posts_once` in a
  genuinely multi-user deployment **must** supply `user_id` (or an equivalent connection scope) on every
  invocation — the optional parameter is a transition mechanism, not a statement that unscoped
  execution is an acceptable steady state once a second real user exists.
- `platform_connections`' `UNIQUE(user_id, platform)` constraint is a real product decision (one
  TikTok account per user), not an incidental default — revisiting it later is a schema migration, not
  a bug fix.
