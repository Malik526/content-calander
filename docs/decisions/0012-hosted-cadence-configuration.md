# ADR-0012: Hosted Posting-Cadence Configuration + Future Slot Generation

## Status

Accepted (Milestone 3.8).

## Context

Every prior milestone's posting cadence (`POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`,
`config.TIMEZONE`) was a global, hardcoded-or-single-env-var setting shared by the whole
deployment, and slot generation (`cli/generate_calendar.py`) ran one calendar month at a
time, on demand, against a single dedicated Google Calendar. Milestone 3.8 builds the
first hosted, per-user version: a user defines a cadence (timezone, active weekdays, one
or more posting times per day), the system generates real `content_slots` rows for a
bounded future horizon, and those slots become the pool a later milestone's FIFO
video-assignment draws from. This ADR records the decisions that shape that model, since
they touch shared schema (`content_slots`) two other subsystems (`scheduling/`,
`calendar/cadence.py`) already depend on.

## Decision 1 — Legacy data stays separate, not merged

Investigation before writing any code found the 9 existing `content_slots`/5
`platform_posts`/5 `videos` rows from CLI usage belong to `user_id=1`
(`local@pickle-batch.local`), which has no `auth_identities` row at all — it can never be
logged into via the hosted web app. The real hosted account
(`malik23stewart23@gmail.com`) is `user_id=2` and owns zero legacy slots. This contradicts
what the milestone's own brief assumed ("legacy CLI-created schedule data already
associated with the current hosted user").

**Decided:** leave them separate. Every 3.8 endpoint scopes to whichever user is
authenticated; `user_id=1`'s legacy rows are preserved exactly as-is and are simply not
part of the new hosted cadence feature. Reassigning ownership is a real product decision
(is the CLI's historical usage "the same person" as the hosted account?) with no clean
answer forced by this milestone, so it was not made here.

## Decision 2 — `content_slots` uniqueness widened to per-user

`content_slots.scheduled_at` had a *global* `UNIQUE` constraint
(`content_slots_scheduled_at_key`), not per-user. Two different hosted users generating a
slot for the same wall-clock timestamp would silently collide — `INSERT OR IGNORE` /
`ON CONFLICT DO NOTHING` semantics mean the second user's slot simply never gets created,
no error. Since 3.8 is exactly the milestone that makes multi-tenant slot generation
real, this was fixed as part of it: widened to `UNIQUE(user_id, scheduled_at)` on both
backends (SQLite: `content_store._migrate_content_slots_to_per_user_uniqueness`, a
rebuild reusing the same `PRAGMA legacy_alter_table=ON` FK-safety technique
`_migrate_content_slots_unique_constraint` already established; Postgres:
`postgres_migrations/0007_hosted_cadence_and_per_user_slot_uniqueness.sql`). This is a
strict widening — every row that satisfied the old constraint trivially satisfies the new
one, so no existing data can violate it.

**Accepted consequence, not engineered around:** SQL `NULL` is never equal to itself for
uniqueness purposes, so a legacy/unscoped (`user_id IS NULL`) row no longer participates
in any uniqueness guarantee at all. Real production data has zero `NULL`-`user_id` rows
(fully backfilled per ADR-0007's own follow-up), so this only affects an intentionally
unscoped test/tooling caller — documented and tested as current, correct behavior
(`tests/test_content_store.py::test_unscoped_insert_slot_if_missing_is_no_longer_idempotent`),
not a regression.

## Decision 3 — Cadence model: one row per user, a child table for times

`posting_cadences` (one active-or-inactive cadence per user, stable `id` across edits —
a `PUT` updates in place, never inserting a second row per user) + `posting_cadence_times`
(the "Monday 09:00, 18:00 / Wednesday 12:00" rows) — plain relational tables, not a JSON
blob column, matching this codebase's existing preference throughout (no JSON columns
exist anywhere else in this schema).

`content_slots` gains two nullable columns: `timezone` (stamped at generation time —
`NULL` for every legacy row, meaning "assume the global `config.TIMEZONE`," identical to
today's actual behavior for those rows) and `cadence_id` (provenance — `NULL` means
manual/legacy, never touched by cadence-edit reconciliation; non-`NULL` means "this exact
`posting_cadences` row generated this slot").

## Decision 4 — Cadence edits reconcile the future `OPEN` slot pool

An earlier draft of this milestone treated slot generation as purely additive (matching
`insert_slot_if_missing`'s existing "row already there always wins" guarantee). Review
caught a real gap: additive-only generation means editing a cadence (different days/times,
or `is_active → false`) leaves the *previous* config's future `OPEN` slots behind
forever — the slot pool would no longer represent the saved cadence at all.

**Decided:** `ContentStoreProtocol.save_cadence_and_regenerate_slots` — one atomic method,
not a composition of separately-committed calls — does all of: upsert the cadence row,
replace its posting times, delete every *future*, *`OPEN`*, *this-cadence's* slot, then
insert the freshly generated horizon. Preserved unconditionally regardless of `cadence_id`:
any slot with status `ASSIGNED`/`PUBLISHED`/`FAILED`, and any slot with `cadence_id IS NULL`
(manual/legacy). Setting `is_active=false` still runs the same reconciliation delete (with
an empty generated-slots list), correctly clearing stale future `OPEN` slots for a paused
cadence.

## Decision 5 — Explicit DST policy for slot generation

`calendar/hosted_cadence.py`'s `generate_slot_datetimes` uses `zoneinfo`, which never
raises on its own for a nonexistent or ambiguous wall-clock time — it silently picks an
interpretation. Two policies made explicit rather than left to accident:

- **Nonexistent local time** (spring-forward gap, e.g. `2:30 AM` on the date a zone jumps
  `2:00→3:00`): detected by round-tripping the candidate through UTC and back and
  comparing wall-clock components — if they don't match, that single slot instance is
  skipped for that day, never silently shifted to another hour.
- **Ambiguous local time** (fall-back, e.g. `1:30 AM` occurring twice): resolved via
  Python's default `fold=0` (the earlier, still-DST instant) — explicit, not accidental.
  Doesn't change what's stored (the naive-local string is identical either way); only
  matters if a future consumer converts `scheduled_at`+`timezone` into a real instant.

Both are tested directly against real transition dates in `tests/test_hosted_cadence.py`.

## Decision 6 — A new module, not an extension of `calendar/cadence.py`

`calendar/cadence.py`'s existing model (global timezone, one calendar month at a time, one
posting time per day) is structurally too narrow for per-user, rolling-horizon,
multiple-times-per-day generation. Rather than overload it, the hosted model lives in a
new `calendar/hosted_cadence.py`, reusing what already applies (`WEEKDAY_NAMES`,
`parse_posting_time`, `ScheduleConfigError`) rather than redefining it.
`calendar/cadence.py` itself is untouched and continues to serve the unrelated CLI/global
path exactly as before.

## Decision 7 — No assignment endpoint in this milestone

`scheduling/slot_matcher.py`'s FIFO/pillar matching and `ContentStore.assign_slot`'s
atomic claim are read-only-referenced by this work, never modified. Milestone 3.8's scope
is schema + generation only (per its own brief); "first eligible uploaded video → earliest
available future slot" keeps meaning exactly what it means today. The new
`content_slots.timezone`/`cadence_id` columns exist so a future milestone building
multi-tenant-aware "now" comparisons and reconciliation has what it needs, without this
milestone touching `scheduling/` at all.

## Consequences

- Two new tables, two new `content_slots` columns, and a widened `content_slots` unique
  constraint, on both backends — a real, if modest, schema migration
  (`postgres_migrations/0007_hosted_cadence_and_per_user_slot_uniqueness.sql`).
- `user_id=1`'s 9 legacy rows remain permanently outside the hosted cadence feature until
  a future, explicitly-scoped decision addresses that mismatch (not part of this
  milestone).
- Nothing in `scheduling/` changed; the FIFO contract downstream milestones will build
  assignment against is exactly what it was before this ADR.
