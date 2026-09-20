# ADR-0008: Postgres Persistence Migration

## Status

Accepted

## Context

Through Milestone 3.2, Pickle Batch's only persistence backend is a local SQLite file
(`data/content.db`), accessed exclusively through `ContentStore`
(`persistence/content_store.py`). Milestone 3.2 made the schema ownership-aware
(`docs/decisions/0007-user-ownership-model.md`) but explicitly kept `user_id` nullable at the
SQL level, because SQLite cannot add a `NOT NULL` foreign-key column to an already-populated
table without a full table rebuild.

Milestone 3.3 replaces SQLite with Postgres as the hosted persistence backend, without yet
building object storage, a real auth-provider integration, hosted background workers, or a
production FastAPI surface — those remain later milestones. The core constraint: the same
business operation (slot assignment, due-post selection, atomic claiming, retries,
reconciliation, crash recovery, tenant scoping, publishing state transitions) must produce the
same outcome regardless of which persistence implementation is behind `ContentStoreProtocol`.

## Decision

### Provider: Supabase Postgres, via its connection pooler

The user already had (created for this purpose) a Supabase project. Supabase Postgres was
evaluated against the brief's own criteria — production Postgres, connection security,
migrations, backups, reasonable early-stage cost (free tier sufficient for this milestone),
Python/psycopg compatibility, future auth/RLS compatibility (Supabase Auth exists but is a
*separate* decision — see "RLS" below), local/dev ergonomics, operational simplicity — and
selected because it satisfies all of them and was already available, not chosen merely for
familiarity.

**Real environment finding, not assumed from documentation:** this sandbox has no outbound
IPv6 (`curl -6` to a known-IPv6 host fails; `socket.has_ipv6` is `True` but nothing routes),
and Supabase's *direct* connection host (`db.<ref>.supabase.co:5432`) resolves IPv6-only —
confirmed by a real connection attempt failing with "Network is unreachable" to a real
resolved IPv6 address, before credentials were ever checked. Supabase's documented fix —
the Session Pooler (`aws-0-<region>.pooler.supabase.com:5432` — Supabase's Session Pooler runs
on port 5432; port 6543 on the same host is the Transaction Pooler, a different mode not used
here, IPv4-compatible either way) — was used instead, and connected successfully
(`PostgreSQL 17.6`, verified directly by parsing only the host/port out of the real
`DATABASE_URL` and confirming `:5432`, never printing the credentials). Any deployment
environment without
outbound IPv6 will hit this same issue with a Supabase direct connection string; the pooler
string is the one this codebase's own `.env.example` documents for that reason.

**Explicitly separate decision, per the brief's own caution:** choosing Supabase Postgres does
not mean choosing Supabase Auth or Supabase Storage. Neither was touched, configured, or even
evaluated this milestone. Real authentication provider selection remains deferred (Milestone
3.2's ADR-0007 "Consequences"); object storage remains Milestone 3.4's decision.

### Driver: psycopg (v3), no ORM

`psycopg[binary]>=3.1` — a maintained, synchronous, minimal Postgres driver. No SQLAlchemy, no
ORM. This codebase already has a persistence boundary (`ContentStore`) and hand-written SQL
throughout; nothing in this migration demonstrated a real need for query-building, relationship
mapping, or migration-framework machinery an ORM would bring — introducing one now would be
exactly the "add SQLAlchemy simply because Postgres is being added" the brief warns against.

### Persistence architecture: two parallel concrete classes, not a shared abstraction layer

`ContentStore` (SQLite, unchanged) and `PostgresContentStore`
(`persistence/postgres_content_store.py`, new) are two independent, complete implementations of
the same method surface — not a base class and two thin subclasses, not a shared query-builder.
`persistence/protocol.py`'s `ContentStoreProtocol` (a `typing.Protocol`, structural not
nominal) documents the shared contract without imposing inheritance on either class or
requiring any change to `ContentStore`'s existing definition.

This mirrors a pattern this codebase already uses: `publishing.tiktok.publisher.TikTokPublisher`
is one concrete implementation of the `Publisher` ABC; a future platform gets its own full
implementation, not a shared parameterized core. The real SQL differences between SQLite and
Postgres (placeholders, `INSERT OR IGNORE` vs. `ON CONFLICT DO NOTHING`, `AUTOINCREMENT` vs.
`GENERATED ALWAYS AS IDENTITY`, no `RETURNING` reliance needed under SQLite vs. `RETURNING *`
used throughout under Postgres, rebuild-based migrations needed under SQLite vs. none needed
under Postgres) are real enough that a shared abstraction over both would itself be a
non-trivial piece of infrastructure, for a benefit (less duplicated SQL) this milestone did not
need.

**`persistence/store_factory.py`'s `build_content_store()`** is the one place a caller that
doesn't care which backend it gets can ask for "the configured one" — `DATABASE_URL` set selects
Postgres, unset selects SQLite, and a broken Postgres connection raises rather than silently
falling back (see "Runtime Configuration" below). No existing CLI entry point was switched to
use it this milestone (every `cli/*.py` still constructs `ContentStore()` directly) — this
milestone proves the Postgres backend works and is tested, not that the SQLite deployment is
being cut over by default.

### Schema: fresh baseline, not a replay of SQLite's migration history

`persistence/postgres_migrations/0001_initial_schema.sql` defines all six tables
(`users`, `auth_identities`, `platform_connections`, `content_slots`, `videos`,
`platform_posts`) in their current, final shape directly — it does not replay
SQLite's own historical migrations (`_migrate_content_slots_unique_constraint`,
`_repair_videos_assigned_slot_fk`, the additive `_ensure_*_columns` calls). Those exist only
because SQLite cannot alter table constraints or FK targets in place; Postgres expresses the
correct shape in one `CREATE TABLE` per table. The one real structural wrinkle:
`content_slots.assigned_video_id` and `videos.assigned_slot_id` reference each other, so
`content_slots` is created first without that FK constraint, `videos` is created second with
its (valid, forward) FK to `content_slots`, and the `content_slots -> videos` FK is added last
via `ALTER TABLE ... ADD CONSTRAINT` — breaking the circular dependency Postgres enforces at
`CREATE TABLE` time (SQLite never validates a `REFERENCES` target exists at all).

### Ownership: `user_id` is `NOT NULL` in Postgres — Milestone 3.2's compromise revisited

Per the brief's own explicit instruction: since every real row was already backfilled to the
bootstrap user under SQLite (Milestone 3.2), Postgres starts from an empty schema and imports
already-ownership-complete data, so there is no transition-period reason to leave `user_id`
nullable here. `videos.user_id`, `content_slots.user_id`, and `platform_posts.user_id` are all
`NOT NULL` in the Postgres schema — verified directly, not merely declared: a raw `INSERT` with
`user_id = NULL` against the real Postgres schema raises `psycopg.errors.NotNullViolation`
(`tests/test_postgres_content_store.py::test_video_insert_without_ownership_is_rejected_by_schema`).

`PostgresContentStore`'s corresponding Python methods (`insert_video`, `insert_slot_if_missing`,
`insert_platform_post`, `insert_platform_post_if_missing`, and every scoped selector/claim/
update method) require `user_id` as a real parameter with no default — a second, earlier layer
of the same guarantee (a `TypeError` before the query is even built), not a substitute for the
database-level constraint.

### Ownership consistency: same application-layer check, now unconditional

`PostgresContentStore.assign_slot()` keeps the same `OwnershipMismatchError` check
`ContentStore.assign_slot()` (SQLite) established in Milestone 3.2, but without the "skip if
either side is `NULL`" carve-out — under Postgres, both sides are always non-null, so the check
is unconditional. No DB-level trigger or cross-table constraint was added: the brief's own
caution against introducing "complex triggers merely to eliminate a well-tested application
check" applies directly, and the existing check is already exercised by both the SQLite (3.2)
and Postgres (3.3) test suites.

### Timestamps: preserved exactly, per-column, not normalized

| Column | Semantic timezone | Postgres type | Python representation | Comparison |
|---|---|---|---|---|
| `scheduled_at` (`content_slots`, `platform_posts`) | naive local (`config.TIMEZONE`) | `TIMESTAMP` (no tz) | `str` (isoformat, naive) | parameterized query; Postgres parses and compares chronologically |
| `next_retry_at` | naive local | `TIMESTAMP` | `str` (naive) | same |
| `created_at`, `updated_at` | aware UTC | `TIMESTAMPTZ` | `str` (isoformat, `+00:00`) | same |
| `published_at`, `next_status_check_at` | aware UTC, nullable | `TIMESTAMPTZ` | `str`/`None` | same |

Nothing is normalized to a single convention — doing so would be a real behavioral change this
migration deliberately does not make (per the brief's own instruction: "do not silently
normalize every field just because Postgres supports timezone-aware types... otherwise preserve
the existing semantics exactly and defer normalization"). Two real mechanisms make this work
correctly, both verified directly against real Supabase Postgres, not assumed:

1. **Session time zone forced to UTC at connect time** (`SET TIME ZONE 'UTC'`, in
   `postgres_content_store._connect`) — without this, a `TIMESTAMPTZ` column's returned
   Python `datetime` would carry whatever time zone the Postgres session defaults to (not
   guaranteed to be UTC on every provider), silently breaking the aware-UTC convention every
   caller depends on.
2. **`_normalize_row()`** converts every returned `datetime.datetime`/`datetime.date` value to
   an isoformat string before constructing a dataclass — psycopg adapts Postgres
   `TIMESTAMP`/`TIMESTAMPTZ` columns to native Python `datetime` objects by default; this
   conversion is what makes `VideoRecord.created_at`, `SlotRecord.scheduled_at`,
   `PlatformPostRecord.updated_at`, etc. come back as the exact same `str`-typed fields the
   SQLite backend (which stores everything as `TEXT`) always returned, so every existing
   caller (`due_post_selector`, `worker`, `reconciliation`, `crash_recovery` — all of which do
   `datetime.fromisoformat(...)` or raw string comparisons) works unmodified against either
   backend. Verified directly:
   `tests/test_postgres_content_store.py::test_timestamps_round_trip_as_aware_utc_strings`.

### Migration framework: plain versioned `.sql` files, not Alembic

A small, dependency-free mechanism (`persistence/postgres_migrate.py`): numbered `.sql` files
under `persistence/postgres_migrations/`, tracked in a `schema_migrations(version, applied_at)`
table (one row per applied filename, created per-schema so the disposable test schema and the
real `public` schema track independently), applied in filename order, each inside its own
transaction. Not Alembic — Alembic depends on SQLAlchemy even for raw-SQL migration bodies,
which conflicts with the same no-ORM preference the driver decision above states; a small
hand-rolled tracker is a well-understood, standard pattern (the same shape tools like
`golang-migrate` use) that delivers "versioned, deterministic, reviewable" migrations (the
brief's own bar) without that dependency. Verified idempotent against real Postgres, not just in
isolation: reopening `PostgresContentStore` against an already-migrated schema applies nothing
new (`tests/test_postgres_content_store.py::test_migrations_are_idempotent_on_reopen`).

### RLS: deferred, server-side ownership scoping is the current boundary

Postgres Row Level Security was evaluated and explicitly **not** enabled this milestone. This
codebase's planned architecture is `client -> FastAPI -> database`, not `browser -> database`
directly (`docs/architecture/hosted-product-boundary.md` §2) — server-side tenant scoping
(Milestone 3.2's `user_id`-scoped selectors, now `NOT NULL`-enforced and proven under real
Postgres concurrency this milestone) is the correct boundary for that shape. Enabling RLS now
would require deciding how a database session obtains the current user's identity before any
real authentication layer exists to supply it (Milestone 3.2's own deferred decision) —
exactly the "do not rely on a future auth mechanism that does not yet exist" the brief warns
against. RLS remains available to add later, without a schema change, once a real
session-identity mechanism exists to drive it.

### Data migration: `cli/migrate_sqlite_to_postgres.py`, IDs preserved, zero TikTok calls

A one-time, idempotent (`ON CONFLICT (id) DO NOTHING`) script, following this codebase's
established one-off-operational-script convention (`backfill_platform_posts.py`,
`migrate_relocated_paths.py`, `backfill_ownership.py`). Copies every row verbatim in dependency
order (`users -> auth_identities -> platform_connections -> content_slots (assigned_video_id
deferred) -> videos -> platform_posts`, then backfills `content_slots.assigned_video_id` in a
second pass — the same circular-dependency-breaking shape the schema itself uses), preserving
every original integer id via `OVERRIDING SYSTEM VALUE`, then advances each table's identity
sequence past the highest imported id (`setval(pg_get_serial_sequence(...), MAX(id), ...)`) —
verified directly that a real subsequent `INSERT` after import gets a fresh, non-colliding id.
Makes zero TikTok API calls: every field is copied from SQLite as-is, nothing is re-derived or
re-validated against a live platform. `verify_migration()` compares row counts per table and
every publishing-critical field (`status`, `platform_post_id`, `scheduled_at`, `failure_reason`,
`retry_count`, `status_check_count`, `user_id`) between source and destination.

### Local development / testing strategy

SQLite remains the default for fast unit tests — `config.DB_PATH`, `ContentStore`, and all 626
pre-3.3 tests are entirely unchanged. Postgres integration tests
(`tests/test_postgres_content_store.py`, `tests/test_store_factory.py`'s Postgres-selecting
test) run only when `config.DATABASE_URL` is set (`pytest.mark.skipif`), against
`config.POSTGRES_TEST_SCHEMA` (`pickle_batch_test` by default) — never `config.POSTGRES_SCHEMA`
("public", the real application schema). The test schema is dropped and recreated once per test
session and every table is `TRUNCATE ... RESTART IDENTITY CASCADE`-reset before each test, so
tests are deterministic and never depend on or interfere with real business rows. This is the
"fast domain/unit tests stay isolated; persistence/concurrency/integration tests run against
real Postgres" split the brief itself suggests as a likely reasonable approach.

### Runtime configuration: no silent fallback

`store_factory.build_content_store()`: `DATABASE_URL` set and reachable -> `PostgresContentStore`;
unset -> `ContentStore` (SQLite); `DATABASE_URL` set but unreachable -> raises
`psycopg.OperationalError`, never silently constructs a SQLite store instead. Verified directly
with a deliberately bogus DSN (`tests/test_store_factory.py`). A real bug was found and fixed
while writing this test: `build_content_store()` originally relied on
`PostgresContentStore.__init__`'s own default parameter value for `dsn` (bound to
`postgres_content_store.DATABASE_URL` at that module's import time) instead of passing
`store_factory`'s own `DATABASE_URL` explicitly — meaning a test (or future runtime override)
that reconfigured only `store_factory.DATABASE_URL` was silently ignored and the real,
working connection string was used instead. Fixed by passing `dsn=DATABASE_URL` explicitly.

### Deferred (explicitly, per guardrails)

Object storage (Milestone 3.4). Hosted worker/scheduler provider. Production FastAPI route
surface. Real Google/Apple login UI. Moving TikTok credential secrets into Postgres or any
hosted store (`platform_connections` still carries identity/status only — see ADR-0007;
nothing about this migration required moving the token itself, and nothing did). Closing the
pre-existing "slot assignment -> platform_post materialization" atomicity seam
(`docs/evaluations/scheduling/milestone-2.1.2-platform-post-materialization.md`) — Postgres
does make this closeable more cheaply than SQLite would, but closing it was not required for
persistence-backend parity and is left as an intentional later improvement, not attempted here
under this milestone's own time/risk budget.

## Consequences

- `ContentStore` (SQLite) is not going away — it remains the default, the fast-test backend,
  and a fully supported implementation of `ContentStoreProtocol`. Nothing in this milestone
  deprecates it.
- Any future method added to either backend that needs to stay usable by both should be added
  to both concrete classes (and to `ContentStoreProtocol` if it's part of the shared contract)
  — there is no single source of truth to edit once and have both backends pick up
  automatically, by design (see "Persistence Architecture" above).
- The next milestone that actually points a running deployment at Postgres must call
  `store_factory.build_content_store()` (or construct `PostgresContentStore` directly) instead
  of `ContentStore()` — no CLI entry point does this automatically as of this milestone.
- Postgres's `NOT NULL` ownership columns are the real, permanent version of the invariant
  Milestone 3.2 approximated under SQLite; any future SQLite-side cleanup should not attempt to
  "catch up" to `NOT NULL` there — SQLite's nullable columns are documented (ADR-0007) as a
  deliberate transitional compromise, not a lagging implementation.
