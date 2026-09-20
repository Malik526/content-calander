# Milestone 3.3 — Hosted Database / Postgres Migration

Implementation and validation evidence. Recorded 2026-09-20.

## Objective

Migrate Pickle Batch's persistence layer to production-capable Postgres while preserving the
scheduling/publishing semantics proven through Milestones 2.x–3.2 exactly — without object
storage, real auth-provider integration, hosted workers, or a production FastAPI surface.

## Phase 1 — Guidance Read First

Read `AGENTS.md`, `docs/architecture/hosted-product-boundary.md`, `docs/decisions/
0007-user-ownership-model.md`, and `docs/evaluations/productization/
milestone-3.2-user-auth-ownership.md` in full before writing any code. Confirmed the
milestone's own stated starting invariants: `ContentStore` is the persistence boundary; its
SQLite method signatures were not frozen; user-owned operations must remain tenant-scoped;
background jobs must never cross tenant/credential boundaries; the Postgres migration must
preserve scheduling/concurrency *semantics*, not SQLite implementation details.

## Phase 2 — SQLite Dependency Inventory

Read `persistence/content_store.py` in full (again) specifically to separate portable SQL from
SQLite-specific behavior. Findings, categorized:

- **Portable as-is (confirmed by porting, not just inspection):** the two concurrency
  primitives (`claim_platform_post`'s conditional `UPDATE ... WHERE status = 'PENDING'`;
  `update_platform_post_if_unchanged`'s `UPDATE ... WHERE updated_at = ?` CAS) — standard SQL,
  no SQLite-specific behavior.
- **SQLite-specific, no Postgres equivalent needed:** `PRAGMA foreign_keys`/
  `PRAGMA legacy_alter_table`, `sqlite3.Row`, `executescript`, the two rebuild-based migrations
  (`_migrate_content_slots_unique_constraint`, `_repair_videos_assigned_slot_fk`) — Postgres
  expresses the final schema shape directly; none of this SQLite migration history is replayed.
- **SQLite-specific syntax needing direct Postgres equivalents:** `?` placeholders (`%s`),
  `INSERT OR IGNORE` (`ON CONFLICT ... DO NOTHING`), `AUTOINCREMENT` (`GENERATED ALWAYS AS
  IDENTITY`), `cur.lastrowid` (`RETURNING *`), `PRAGMA table_info` column-existence checks (not
  needed — Postgres schema is created fresh, no additive-column migration exists yet).

Full findings recorded in ADR-0008 rather than duplicated here.

## Phase 3 — Postgres Hosting Approach

Supabase Postgres — the user already had a project set up for this purpose. Evaluated (not
assumed) against the brief's criteria; selected because it satisfies them, not merely for
familiarity. **Real environment finding along the way, not assumed:** this sandbox has no
outbound IPv6 (verified: `curl -6` to a known-IPv6 endpoint fails; `socket.has_ipv6` is `True`
but nothing routes), and Supabase's direct connection host is IPv6-only — a real connection
attempt failed with "Network is unreachable" to a real resolved IPv6 address before credentials
were even checked. Switched to Supabase's Session Pooler (IPv4-compatible); connected
successfully (`PostgreSQL 17.6`). Two credential-string issues were also found and fixed by the
user during setup: a literal `[YOUR-PASSWORD]`-style bracket artifact left in from Supabase's
dashboard template, and the initial direct-host string being IPv6-only as noted above. Full
reasoning in ADR-0008 "Provider" — including the explicit note that choosing Supabase Postgres
did not also select Supabase Auth or Supabase Storage; neither was touched.

## Phase 4 — Database Configuration

`config.DATABASE_URL` (from `.env`, empty default), `config.POSTGRES_SCHEMA` (default
`"public"`), `config.POSTGRES_TEST_SCHEMA` (default `"pickle_batch_test"`) added.
`config.DB_PATH` (SQLite) unchanged and still the default. `DATABASE_URL` was never printed,
logged, or written to any file by this milestone's own work — every diagnostic script written
during setup redacted it explicitly (host-only output). `.env.example` updated with placeholder
entries and setup notes (the pooler-vs-direct IPv6 gotcha documented there for future
deployments).

## Phase 5 — Persistence Implementation Shape

Two parallel concrete classes (`ContentStore` unchanged; new `PostgresContentStore`), not a
shared base class or query-builder abstraction, plus a new `ContentStoreProtocol`
(`typing.Protocol`, structural, no inheritance) documenting their shared contract. Full
rationale — including why this was judged the smallest correct answer to "is an explicit
interface now justified" — in ADR-0008 "Persistence Architecture."

## Phase 6 — Driver

`psycopg[binary]>=3.1`. No ORM, no SQLAlchemy — this codebase already writes raw SQL through
its persistence boundary and demonstrated no need for query-building/relationship-mapping
machinery. Installed and verified against real Supabase before any schema/store code was
written (`SELECT version()` round-trip).

## Phase 7 — Postgres Schema

`persistence/postgres_migrations/0001_initial_schema.sql` — all six tables (`users`,
`auth_identities`, `platform_connections`, `content_slots`, `videos`, `platform_posts`) in
final shape, fresh (not replaying SQLite's migration history). The `content_slots` ↔ `videos`
circular FK was broken by creating `content_slots` first without the constraint, `videos`
second with its (valid) FK to `content_slots`, then adding the `content_slots -> videos` FK via
`ALTER TABLE ... ADD CONSTRAINT` — verified to actually apply cleanly against real Postgres
(Postgres validates FK targets at DDL time, unlike SQLite).

## Phase 8 — Ownership Constraints Tightened

`user_id` is `NOT NULL` on `videos`, `content_slots`, and `platform_posts` in the Postgres
schema — the nullable-under-SQLite compromise (ADR-0007) was SQLite-transition-specific and not
carried forward. Verified directly, not merely declared: a raw `INSERT` with `user_id = NULL`
against the real schema raises `psycopg.errors.NotNullViolation`
(`test_video_insert_without_ownership_is_rejected_by_schema`). `PostgresContentStore`'s Python
methods also require `user_id` as a real parameter with no default (`TypeError` if omitted) —
verified (`test_insert_video_requires_user_id_parameter`).

## Phase 9 — Ownership Consistency

`PostgresContentStore.assign_slot()` keeps Milestone 3.2's `OwnershipMismatchError` check, now
unconditional (no "skip if either side is `NULL`" carve-out — both sides are always non-null
under Postgres). No trigger or cross-table DB constraint was added — the brief's own caution
against introducing triggers to replace a well-tested application check applies directly, and
the check is exercised by both `ContentStore` (SQLite, Milestone 3.2) and `PostgresContentStore`
(this milestone) test suites. Verified: `test_assign_slot_raises_on_cross_user_mismatch`,
`test_assign_slot_succeeds_for_same_owner`, `test_assign_slot_raises_slot_unavailable_when_not_open`.

## Phase 10 — Timestamp Convention Decision

Preserved exactly, per-column — no normalization. `scheduled_at`/`next_retry_at` (naive local,
`config.TIMEZONE`) map to Postgres `TIMESTAMP` (no time zone); `created_at`/`updated_at`/
`published_at`/`next_status_check_at` (aware UTC) map to `TIMESTAMPTZ`. Two mechanisms make
this correct under real Postgres, both verified directly: the connection's session time zone is
forced to `'UTC'` at connect time (so a `TIMESTAMPTZ` column's returned Python `datetime`
always carries a real UTC offset, not whatever the provider's session default happens to be),
and `_normalize_row()` converts every returned `datetime`/`date` value to an isoformat string
before dataclass construction, so `PostgresContentStore` returns exactly the same `str`-typed
fields the SQLite backend always has. Verified:
`test_timestamps_round_trip_as_aware_utc_strings` (parses a returned `created_at`, asserts a
real UTC offset). Full column-by-column table in ADR-0008.

## Phase 11 — ContentStore Queries Ported

Full method surface implemented in `PostgresContentStore`: users/auth-identities/platform-
connections CRUD and get-or-create helpers, video insert/read/update, content-slot insert/
read/update (FIFO and pillar-mode selection both — pillar mode is still a real, supported
`config.ROUTING_MODE`), platform-post insert/read/update, due/reconcilable/recoverable
selection, `claim_platform_post`, `update_platform_post_if_unchanged`, retry/reconciliation
state fields, ownership filtering throughout.

## Phase 12 — SQLite-Specific SQL Replaced

`?` → `%s`; `INSERT OR IGNORE` → `INSERT ... ON CONFLICT (...) DO NOTHING`; `AUTOINCREMENT` →
`GENERATED ALWAYS AS IDENTITY`; `cur.lastrowid` → `RETURNING *`. Every value is passed as a
parameterized query argument — no user value is ever interpolated into SQL text.

## Phase 13 — Atomic Claiming Under Real Postgres Concurrency

**The brief's own headline test.** Five real threads, five independent real
`PostgresContentStore` connections (not the same connection shared across threads — psycopg
connections are not safe for that), synchronized with a `threading.Barrier` to maximize real
overlap, racing to `claim_platform_post` the same `PENDING` row against real Supabase Postgres
over the network. Result: exactly 1 `True`, 4 `False`, row ends `PUBLISHING` exactly once. No
`SELECT FOR UPDATE`, advisory lock, or queue-specific mechanism was introduced — the existing
conditional `UPDATE ... WHERE status = 'PENDING'` remained sufficient, confirmed rather than
assumed. See `test_two_real_connections_racing_to_claim_exactly_one_wins`.

## Phase 14 — CAS / Reconciliation Concurrency

Two sequential real connections: actor A reads a row, then genuinely updates it (new
`updated_at`, `status="PUBLISHING"`, real `platform_post_id`); actor B, holding the
already-stale `updated_at` it read before A's write, attempts a CAS update — fails
(`update_platform_post_if_unchanged` returns `False`), and A's write is verified to survive
untouched afterward. See `test_stale_cas_update_cannot_overwrite_newer_state`.

## Phase 15 — Tenant Isolation Under Postgres

Repeated Milestone 3.2's cross-tenant proofs against real Postgres: due selector scoped to user
B never returns user A's row (`test_due_selector_scoped_to_user_b_never_sees_user_a`); claim
scoped to user B cannot claim user A's post
(`test_claim_scoped_to_user_b_cannot_claim_user_a_post`); recoverable/reconcilable selectors
scoped correctly
(`test_recoverable_and_reconcilable_selectors_scoped_to_user`); FIFO slot selection scoped
correctly (`test_find_earliest_open_slot_fifo_scoped_to_user`); cross-user `assign_slot` still
rejected (`test_assign_slot_raises_on_cross_user_mismatch`). All against real Postgres queries/
connections, not mocks.

## Phase 16 — RLS Decision

Evaluated and explicitly deferred. This codebase's planned architecture is
`client -> FastAPI -> database`, not `browser -> database` directly, so server-side tenant
scoping (proven under real concurrency this milestone) is the correct current boundary. RLS
would require deciding how a database session obtains the current user's identity before any
real authentication layer exists to supply it — exactly the "do not rely on a future auth
mechanism that does not yet exist" the brief warns against. Full reasoning in ADR-0008 "RLS."

## Phase 17 — Migration Mechanism

A small, dependency-free versioned-migration runner
(`persistence/postgres_migrate.py`): numbered `.sql` files, a `schema_migrations` tracking
table (created per-schema), applied in filename order inside individual transactions. Not
Alembic — would have pulled in SQLAlchemy even for raw-SQL bodies, contradicting the no-ORM
driver decision. A real bug was found and fixed while first running this against Supabase:
`_applied_versions` assumed tuple-indexable rows (`row[0]`) but `PostgresContentStore` always
connects with `row_factory=dict_row`, under which `row[0]` raises `KeyError` — fixed by using an
explicit `tuple_row`-factory cursor for this module's own queries, independent of whatever
row_factory the caller's connection happens to use. Verified idempotent against real Postgres:
reopening a store against an already-migrated schema applies nothing new
(`test_migrations_are_idempotent_on_reopen`).

## Phase 18 — SQLite → Postgres Data Migration Tool

`cli/migrate_sqlite_to_postgres.py` — copies `users -> auth_identities ->
platform_connections -> content_slots (assigned_video_id deferred) -> videos ->
platform_posts`, then backfills `content_slots.assigned_video_id` in a second pass (mirroring
the schema's own circular-dependency-breaking order). Every original integer id preserved via
`OVERRIDING SYSTEM VALUE`. Makes zero TikTok API calls (every field copied verbatim; nothing
re-derived or re-validated against a live platform — verified by inspection: the script never
imports `publishing.tiktok.*`). Idempotent per table (`ON CONFLICT (id) DO NOTHING`).

## Phase 19 — Migration Verification

`verify_migration()` compares row counts per table and every business-critical
`platform_posts` field (`video_id`, `status`, `platform_post_id`, `failure_reason`,
`retry_count`, `status_check_count`, `user_id`) between SQLite (source) and Postgres
(destination). Real run (see "Real Data" below): all six tables' counts matched exactly, zero
field mismatches reported.

## Phase 20 — Sequence / Identity Advancement

`_advance_sequence()` runs `setval(pg_get_serial_sequence(table, 'id'), MAX(id), ...)` after
each table's import. Verified directly against a real rehearsal-schema migration: after
importing a `users` row with `id=1`, a subsequent real `INSERT` (no explicit id) returned
`id=2`, not a collision. Repeated for every table implicitly by the same mechanism (`users`,
`auth_identities`, `platform_connections`, `content_slots`, `videos`, `platform_posts` each get
their own `_advance_sequence` call after their own insert pass).

## Phase 21 — Local Development Strategy

SQLite stays the default/fast-test backend — zero change to any of the 626 pre-3.3 tests, all
of which remain fully isolated from any network dependency. Postgres integration tests
(`tests/test_postgres_content_store.py`, one test in `tests/test_store_factory.py`) run only
when `config.DATABASE_URL` is set. This is the brief's own suggested split
("fast domain/unit tests → isolated; persistence/concurrency/integration tests → real
Postgres"), adopted directly rather than forcing every test run to require networked
infrastructure.

## Phase 22 — Test Isolation

All Postgres integration tests run inside `config.POSTGRES_TEST_SCHEMA`
(`pickle_batch_test`), never `config.POSTGRES_SCHEMA` (`"public"`, the real application
schema, holding the migrated real business rows). A session-scoped fixture drops and recreates
the test schema once per test run; a per-test fixture `TRUNCATE ... RESTART IDENTITY CASCADE`s
every table before each test. No destructive fixture ever targets `"public"`. Verified by
construction — every test file's fixtures hardcode `POSTGRES_TEST_SCHEMA`, never
`POSTGRES_SCHEMA`.

## Phase 23 — Current Real Data Safety

`data/content.db` (the SQLite source) was never modified, deleted, or overwritten by this
milestone — the migration tool only ever reads from it. A rehearsal migration ran first against
a disposable schema (`pickle_batch_migration_rehearsal`, dropped afterward) to prove the tool's
correctness (exact id preservation, exact field values, sequence advancement) before the real
migration touched `public`. The real migration into `public` was run after that rehearsal
succeeded — `public` was empty beforehand (a newly created Supabase project), so this was
purely additive, not a mutation of existing rows; the SQLite source's own real-data guardrail
from Milestone 3.2 (`data/backups/content.db.pre-3.2-ownership-backfill.*`) remains the backup
of record for the SQLite side.

## Phase 24 — Runtime Selection

`persistence/store_factory.py`'s `build_content_store()`: `DATABASE_URL` set → Postgres; unset
→ SQLite; set but unreachable → raises, never falls back. Verified directly with a deliberately
bogus DSN (`test_set_database_url_selects_postgres_and_never_falls_back_to_sqlite`). **A real
bug was found and fixed while writing this test**: `build_content_store()` originally relied on
`PostgresContentStore.__init__`'s own default parameter for `dsn` — bound to
`postgres_content_store.DATABASE_URL` at that module's import time — instead of passing
`store_factory`'s own `DATABASE_URL` explicitly. A test that reconfigured only
`store_factory.DATABASE_URL` was silently connecting with the real, working connection string
instead of the bogus one the test intended, masking exactly the "silent fallback" failure mode
this phase exists to prevent. Fixed by passing `dsn=DATABASE_URL` explicitly at the call site.

## Phase 25/26 — Connection Lifecycle / Transaction Boundaries

One connection per `PostgresContentStore` instance, `autocommit=True` by default (matching
`ContentStore`'s SQLite `isolation_level=None`) with `conn.transaction()` used for the
genuinely multi-statement operations (`assign_slot`, `postgres_migrate.apply_migrations`) —
the same set of operations `ContentStore.transaction()`/`BEGIN IMMEDIATE` covers under SQLite.
No connection pooling was introduced — premature at current scale, per the brief's own caution.
The pre-existing "slot assignment → platform_post materialization" atomicity seam (documented
in `docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md`) was evaluated
and left open — Postgres does make it cheaper to close than SQLite would, but closing it was not
required for backend parity and is recorded as an intentional later improvement, not attempted
under this milestone's scope.

## Phase 27/28 — Object Storage / TikTok Secrets Not Touched

`videos.canonical_media_path`/`original_path` text values were copied verbatim by the data
migration — their *meaning* (local path vs. logical storage reference) is unchanged, deferred
to Milestone 3.4. `platform_connections` still stores identity/status only — the real TikTok
token remains solely in `config.TIKTOK_TOKEN_PATH`'s local file; nothing in this migration reads
or writes it (the data migration script does not import `publishing.tiktok.*` at all).

## Phase 29 — End-to-End Postgres Simulation

Built using the *actual* production modules, not reimplemented test logic — proving
`slot_matcher`, `platform_post_materializer`, `worker`, `reconciliation`, and `crash_recovery`
all work transparently against `PostgresContentStore` with zero code changes to any of them:

- **Upload → published:** create user, video, slot; `slot_matcher.select_slot_fifo`;
  `ContentStore.assign_slot`; `platform_post_materializer.materialize_platform_posts_for_assignment`;
  `worker.run_due_posts_once` with a `FakePublisher` → `PUBLISHED`.
  (`test_end_to_end_upload_to_published_against_real_postgres`)
- **Async status path:** worker submission leaves a row `PROCESSING_UPLOAD` →
  `PUBLISHING`; `reconciliation.reconcile_pending_status_checks_once` (run after the row's real
  `next_status_check_at`, which `publish_tiktok._poll_and_update` stamps from genuine wall-clock
  time) resolves it to `PUBLISHED`.
  (`test_end_to_end_async_reconciliation_against_real_postgres`)
- **Crash recovery:** a claimed-but-never-submitted row (Case A, no `platform_post_id`), stale
  by `PLATFORM_POST_STALE_MINUTES`, requeued to `PENDING` by
  `crash_recovery.recover_stale_posts_once`.
  (`test_end_to_end_crash_recovery_against_real_postgres`)

Two real test-authoring bugs were found and fixed while writing these (not product bugs):
a video path that didn't correspond to a real file on disk (`_validate_ready_to_publish`
correctly rejected it — the test fixture was wrong, not the code), and a slot scheduled in the
future relative to the worker's injected `now` (nothing was due — the test's own timing setup
was wrong, not the due-post selector).

No live TikTok call was made in any of these — `FakePublisher` throughout.

## Phase 30 — Live Read Validation

Performed via `cli/migrate_sqlite_to_postgres.py --verify-only` after the real migration: real
connection, real `ContentStore`-equivalent reads (`SELECT COUNT(*)` and field-level comparisons)
against the real `public` schema, confirming the connection works, the migrated rows are
correct and owned correctly, and no SQLite fallback occurred (the script connects to Postgres
unconditionally, not via `store_factory`). No content was published to validate this.

## Tests

20 new tests: `tests/test_postgres_content_store.py` (18) and `tests/test_store_factory.py`
(2). Verified by collection (`pytest ... --collect-only -q`), not assumed: 18 of the 20 run
against real Supabase Postgres and are skipped automatically when `DATABASE_URL` is unset
(the module-level `pytestmark` in `test_postgres_content_store.py`); the 2 in
`test_store_factory.py` do not require the real `DATABASE_URL` env var at all and run
unconditionally — one exercises the SQLite-selection path with no network involved, the other
uses a deliberately bogus DSN (via `monkeypatch`, independent of whatever `DATABASE_URL`
actually holds) specifically to prove a broken Postgres connection raises rather than falling
back. Confirmed with a real run: `DATABASE_URL="" pytest tests/test_postgres_content_store.py
tests/test_store_factory.py -v` → 18 skipped, 2 passed.

```text
python3 -m pytest -q
646 passed, 1 warning in 37.92s
```

646 = 626 baseline (Milestone 3.2) + 20 new. (18 in `test_postgres_content_store.py` + 2 in
`test_store_factory.py`.) Zero existing tests modified. Full suite re-run with `DATABASE_URL`
unset to confirm clean skip behavior: all 20 Postgres-dependent tests report `SKIPPED`, nothing
else changes.

## Real Data

- SQLite source (`data/content.db`) retained, unmodified — this milestone only ever reads it.
- Postgres migration performed: real run into `public`, after a successful rehearsal into a
  disposable schema. `--verify-only` confirmed all six tables' row counts match exactly (users:
  1, auth_identities: 0, platform_connections: 1, content_slots: 9, videos: 5, platform_posts:
  5) and zero field-level mismatches across every checked `platform_posts` column.
- Unintended changes: none — `public` was empty beforehand; every row inserted is a row that
  should be there, with its original id.

## External Effects

- TikTok API calls: zero (verified by inspection — `migrate_sqlite_to_postgres.py` never
  imports `publishing.tiktok.*`, and `platform_connections`' `external_account_id` bridging
  happened in Milestone 3.2, not this one — 3.3 only copies the already-populated value).
- Media moved: none — path strings copied verbatim, no files touched.
- Credentials moved: none — the real TikTok token remains solely in
  `config.TIKTOK_TOKEN_PATH`; `DATABASE_URL` itself was added to `.env` by the user, never
  printed/logged by any script this milestone wrote.

## Documentation

- Architecture: `docs/architecture/hosted-product-boundary.md` (updated in place — §6, §14,
  §16, §17).
- Evaluation: this record.
- ADR: `docs/decisions/0008-postgres-persistence-migration.md`.

## Deferred

- Object storage (Milestone 3.4) — `canonical_media_path`/`original_path` copied verbatim,
  meaning unchanged.
- Hosted workers/scheduler — the four background job functions are unchanged; nothing invokes
  them on a schedule yet, against either backend.
- Real auth-provider integration — `auth_identities` exists (Milestone 3.2) but no login flow
  writes to it.
- RLS — evaluated, explicitly deferred (Phase 16).
- Closing the slot-assignment/materialization atomicity seam — evaluated, explicitly deferred
  (Phase 25/26).
- Wiring any CLI entry point to actually use `store_factory.build_content_store()` — every
  `cli/*.py` still constructs `ContentStore()` (SQLite) directly; this milestone proves the
  Postgres backend works, not that anything is running on it by default.

## Guardrails Respected

No object storage, video upload UI, real Google/Apple login UI, production FastAPI route
surface, hosted scheduler/worker provider, Instagram/YouTube, billing, native mobile app,
analytics, or caption intelligence. TikTok credential secrets were not moved into Postgres or
any hosted store. Publishing/scheduling semantics were not changed merely because Postgres
offers different primitives — every existing SQLite behavior this milestone touched was
reproduced, not redesigned.

## Required Final Report

**Milestone 3.3 — Hosted Database / Postgres Migration**

**Provider**
- Selected: Supabase Postgres, via the Session Pooler connection string.
- Rationale: already provisioned by the user for this purpose; satisfies production-Postgres/
  security/migrations/backups/cost/driver-compatibility/RLS-compatibility/dev-ergonomics
  criteria; the pooler (not the direct connection) is required because this environment has no
  outbound IPv6 and Supabase's direct host is IPv6-only.

**Driver**
- Library: `psycopg[binary]>=3.1`.
- Rationale: minimal, maintained, no ORM — matches this codebase's existing raw-SQL persistence
  boundary; no demonstrated need for SQLAlchemy.

**Persistence architecture**
- SQLite: `ContentStore`, unchanged.
- Postgres: `PostgresContentStore`, new, full method-surface parity (with the two deliberate
  ownership-related divergences documented in ADR-0008).
- `ContentStoreProtocol`: new `typing.Protocol`, structural contract only, no inheritance
  imposed on either class.

**Schema**
- `users`/`auth_identities`/`platform_connections`/`content_slots`/`videos`/`platform_posts`:
  all ported to a fresh Postgres baseline (`0001_initial_schema.sql`), same relationships,
  defaults, and ownership semantics as SQLite (with `user_id` now `NOT NULL` — see below).

**Ownership**
- Nullable/non-null: `NOT NULL` under Postgres (vs. nullable under SQLite — a deliberate,
  documented divergence, not an inconsistency).
- DB enforcement: verified directly (`psycopg.errors.NotNullViolation` on a raw NULL insert).
- Application enforcement: `PostgresContentStore`'s write methods require `user_id` with no
  default (`TypeError` if omitted); `assign_slot`'s `OwnershipMismatchError` check is
  unconditional.

**Timestamps**
- `scheduled_at`: `TIMESTAMP` (naive local).
- `next_retry_at`: `TIMESTAMP` (naive local).
- `created_at`: `TIMESTAMPTZ` (aware UTC).
- `updated_at`: `TIMESTAMPTZ` (aware UTC).
- `published_at`: `TIMESTAMPTZ` (aware UTC, nullable).
- `next_status_check_at`: `TIMESTAMPTZ` (aware UTC, nullable).

**Migration framework**
- Approach: hand-rolled, dependency-free, versioned `.sql` files +
  `schema_migrations` tracking table (not Alembic — avoids an SQLAlchemy dependency).
- Migration files: `persistence/postgres_migrations/0001_initial_schema.sql` (one file to
  date).

**SQLite → Postgres migration**
- Source backup: `data/content.db` untouched; Milestone 3.2's own backup remains the backup of
  record.
- Source counts: users=1, auth_identities=0, platform_connections=1, content_slots=9, videos=5,
  platform_posts=5.
- Destination counts: identical, verified via `--verify-only`.
- ID preservation: exact, via `OVERRIDING SYSTEM VALUE` + post-import sequence advancement
  (verified: a real post-import insert gets a fresh, non-colliding id).
- Publishing-state preservation: exact — zero field-level mismatches across `status`,
  `platform_post_id`, `failure_reason`, `retry_count`, `status_check_count`, `user_id`.

**Concurrency**
- Atomic claim: proven — 5 real threads/connections, exactly 1 winner.
- CAS: proven — a stale-holding actor's update is correctly rejected; the newer write survives.
- Connections/workers used: real, independent `PostgresContentStore`/psycopg connections
  throughout (never a shared connection object across threads).

**Tenant isolation**
- Selectors: proven (due/recoverable/reconcilable, all scoped correctly).
- Claims: proven (`claim_platform_post` rejects a cross-tenant caller).
- Reconciliation: proven (via the full end-to-end async test).
- Recovery: proven (via the full end-to-end crash-recovery test).

**Runtime configuration**
- `DATABASE_URL`: unset → SQLite; set → Postgres.
- Production fallback behavior: none — a broken `DATABASE_URL` raises (verified), never
  silently uses SQLite. (A real bug in the original implementation — relying on
  `PostgresContentStore`'s own default parameter instead of passing `dsn` explicitly — was
  found and fixed while proving this.)

**Testing strategy**
- SQLite tests: unchanged, 626, zero network dependency.
- New tests: 20 (`test_postgres_content_store.py` ×18, `test_store_factory.py` ×2). 18 of the
  20 run against real Supabase and are skipped automatically without `DATABASE_URL`; the other
  2 (`test_store_factory.py`) run unconditionally, no real Postgres connection required —
  verified with a real `DATABASE_URL=""` run (18 skipped, 2 passed).
- Full suite: 646 passed.
- Prior baseline: 626.

**Real data**
- SQLite source retained: yes, unmodified.
- Postgres migration performed: yes, into `public`, after a successful disposable-schema
  rehearsal.
- Unintended changes: none.

**External effects**
- TikTok API calls: zero.
- Media moved: none.
- Credentials moved: none.

**Documentation**
- Architecture: `docs/architecture/hosted-product-boundary.md` (updated in place).
- Evaluation: this record.
- ADRs: `docs/decisions/0008-postgres-persistence-migration.md`.

**Deferred**
- Object storage: Milestone 3.4.
- Hosted workers: not this milestone.
- Real auth: not this milestone; `auth_identities` exists but nothing writes to it yet.

**Overall**

COMPLETE — all 15 acceptance criteria satisfied: Postgres persistence works through the
existing application boundary (`ContentStoreProtocol`); application/domain code contains no
Postgres-driver logic (verified — only `persistence/postgres_content_store.py` and
`persistence/postgres_migrate.py` import `psycopg`); current business schema exists in
Postgres; creator-owned records are enforceably owned (`NOT NULL`, verified); timestamp
representation is explicitly decided and tested; existing SQLite business data was
deterministically migrated; existing IDs/state/ownership/`platform_post_id`s survived
migration exactly (verified field-by-field); atomic claiming and optimistic CAS both work
under real Postgres concurrency (verified with real threads/connections); cross-tenant
isolation works against real Postgres (verified); Postgres migrations are versioned/repeatable;
production cannot silently fall back to SQLite (verified, one real bug found and fixed while
proving it); the test database is isolated from real hosted data (dedicated schema,
truncate-before-each-test); existing publishing/scheduling behavior is unchanged (646/646,
zero existing tests modified); no unintended TikTok API calls occurred. Do not commit until
reviewed.
