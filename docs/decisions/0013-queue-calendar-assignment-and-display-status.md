# ADR-0013: Queue Video-to-Slot Assignment + Display-Status Derivation

## Status

Accepted (Milestone 3.9).

## Context

Milestone 3.8/3.8.1 built cadence configuration and future `content_slots` generation, but
deliberately stopped short of any real assignment: no endpoint existed to put an
already-uploaded video into a slot, no way to reverse that, and the Queue page only showed
the cadence editor itself. Milestone 3.9 builds that missing middle layer — "Queue = videos
occupying [cadence-generated] slots" — on top of `assign_slot`/`find_earliest_open_slot_fifo`,
which already existed for the CLI ingestion pipeline (`media/processing.py`) but had never
been reachable from the hosted API for an already-uploaded video.

## Decision 1 — `content_slots.status` never becomes PUBLISHED/FAILED; `display_status` is derived at the read boundary

Investigation before writing any code found that, despite `content_slots.status`'s schema
comment implying four possible values (`OPEN`/`ASSIGNED`/`PUBLISHED`/`FAILED`), the only two
`UPDATE content_slots ... SET status` statements anywhere in this codebase are both inside
`assign_slot()`, both writing `'ASSIGNED'`. No code path — including `scheduling/worker.py`,
which owns the real publish lifecycle — has ever written `PUBLISHED` or `FAILED` onto a
`content_slot`. The real outcome lives on `platform_posts.status`
(`PENDING`/`PUBLISHING`/`PUBLISHED`/`FAILED`), exactly as ADR-0005 anticipated when it
deferred a "Publishing-readiness state model" as future scope rather than building one then.

**Decided:** leave `content_slots.status` exactly as-is (`OPEN`/`ASSIGNED` only, `scheduling/`
untouched). `GET /api/queue/slots` (`api/routes/queue.py`) computes a separate
`display_status` field per response row by looking up the assigned video's `platform_posts`
row(s) at read time — `PUBLISHED`/`FAILED`/`PUBLISHING` take precedence over `ASSIGNED` when
present. This keeps the real, already-validated `scheduling/` publish/retry/reconciliation
semantics completely untouched (a hard AGENTS.md guardrail) while still letting the Queue UI
show a real published/failed state the moment `worker.py` produces one — no polling or
duplicate state to keep in sync, since it's derived, not stored.

## Decision 2 — `unassign_slot` only reverses what's safely reversible

"Remove from schedule" needs to be the true inverse of assignment (`assign_slot` +
`materialize_platform_posts_for_assignment`), but only up to the point nothing irreversible
has happened. A still-`PENDING` `platform_posts` row is pure intent — deleting it and
reopening the slot is safe and, in fact, necessary: `insert_platform_post_if_missing` never
overwrites an existing row, so leaving a stale `PENDING` row behind would silently break a
future reassignment of the same video (it would keep the old slot's `scheduled_at` forever).
A `PUBLISHING`/`PUBLISHED`/`FAILED` row is real history — a publish was actually attempted or
completed — and must never be silently erased.

**Decided:** new `ContentStoreProtocol.unassign_slot(slot_id)` (mirroring `assign_slot`'s own
one-atomic-transaction shape on both backends): resets the slot to `OPEN`, clears
`assigned_video_id`/`videos.assigned_slot_id`, and deletes the video's `platform_posts` row
*only if* it's still `PENDING`. Raises the new `PlatformPostInProgressError` — refusing,
without changing anything — if any row for that video is `PUBLISHING`/`PUBLISHED`/`FAILED`.
This directly satisfies the milestone's "preserve published/history records safely"
requirement and is why Delete Video's existing 409 guard (`VideoHasScheduleReferencesError`,
Milestone 3.7 follow-up) becomes satisfiable again after a clean removal but stays correctly
blocked after a real publish attempt — no change to that guard itself was needed or made.

## Decision 3 — a video already occupying a slot can't be assigned to a second one

`assign_slot()` itself only checks the *slot's* availability, not whether the *video* already
has an `assigned_slot_id`. Left unchecked, assigning an already-scheduled video to a second
slot would silently leave the first slot stuck `ASSIGNED` to a video whose own
`assigned_slot_id` now points elsewhere — a real double-booking / orphaned-slot bug.

**Decided:** the new `scheduling/queue_assignment.py` module (not `assign_slot` itself, to
avoid widening that primitive's narrow, already-tested contract — the same reasoning
`platform_post_materializer.py` documented for staying a separate step) checks this and
raises `VideoAlreadyScheduledError` before ever calling `assign_slot`, for both the manual
and automatic/FIFO assignment paths. The caller must remove the video from its current
schedule first.

## Decision 4 — a new `scheduling/` module, not a widened `assign_slot` or a new `calendar/` file

Both manual assignment (a caller-chosen slot) and automatic/FIFO assignment (the caller
picks the video, the earliest eligible `OPEN` slot is found via the *existing*
`scheduling.slot_matcher.select_slot_fifo` — not a second scheduler) are "when/whether a
platform post executes" concerns per AGENTS.md's own package-boundary rule, so they belong in
`scheduling/`, not `calendar/`. `scheduling/queue_assignment.py` glues `assign_slot` +
`materialize_platform_posts_for_assignment` together exactly the way
`media/processing.py`'s local-ingestion pipeline already does, so an already-uploaded hosted
video can be assigned the same way outside that pipeline, without a second, divergent
implementation of "what happens after a slot is claimed."

## Consequences

- Two new `content_store.py`/`postgres_content_store.py` methods (`unassign_slot`), one new
  scheduling module (`queue_assignment.py`), one new route file (`api/routes/queue.py`) with
  three endpoints (`GET /api/queue/slots`, `POST /api/queue/slots/{id}/assign`,
  `POST /api/queue/assign-next`, `POST /api/queue/slots/{id}/unassign`) — no schema migration
  was needed; every column this milestone reads/writes already existed.
- `GET /api/cadence/slots` (Milestone 3.8) is untouched and still works, but the Queue page no
  longer calls it — `GET /api/queue/slots` is strictly richer (assigned video, derived
  display status) and is what `components/app/QueueBoard.tsx` uses instead.
- `content_slots.status` staying two-valued forever (until a real "Publishing-readiness state
  model" milestone, if one is ever built) is now a load-bearing, documented fact — a future
  change that starts writing `PUBLISHED`/`FAILED` onto `content_slots` directly would make
  `display_status`'s derivation redundant, not wrong, but should update this ADR if it happens.
- No calendar/queue editing beyond assign/manual-assign/remove — no drag/drop, no manual
  one-off slot creation, no rescheduling, no published-history browsing UI. Still future scope.

## Addendum — Milestone 3.11: one publish-status resolver, sanitized failures, NEEDS_ATTENTION

Decision 1 still holds: `content_slots.status` is never written past `ASSIGNED`, and display
state is derived at read time. 3.11 moves that derivation out of `api/routes/queue.py` into
`publishing/publish_status.py`, the one platform-neutral resolver, and widens it:

- **Values:** `OPEN`/`SCHEDULED`/`PUBLISHING`/`PUBLISHED`/`FAILED`/`NEEDS_ATTENTION`.
  `SCHEDULED` replaces 3.9's `ASSIGNED` display value.
- **`PUBLISHED` requires a platform post ID.** The real publish path always persists it before
  polling, so a `PUBLISHED` row without one is contradictory.
- **`NEEDS_ATTENTION` replaces any guess.** It applies to a missed schedule (PENDING past
  `scheduled_at` plus `PUBLISH_OVERDUE_GRACE_MINUTES`, with no future retry pending), a stalled
  claim, an unconfirmed submission (staleness uses crash recovery's
  `PLATFORM_POST_STALE_MINUTES`), contradictory rows, and an assigned video with no
  `platform_posts` row. The resolver's docstring lists every trigger.
- **Multiple platforms:** each post resolves on its own (`publications`). The slot shows the
  most urgent state, and `PUBLISHED` only when every post is published.

**Failure explanations come from a new `platform_posts.failure_code` column, never
`failure_reason`.** `failure_reason` is raw exception text, sometimes embedding API response
bodies. It stays internal and is no longer appropriate to surface or parse. This revisits
Milestone 2.1.6's "no error-code column" decision (made when nothing user-facing existed).

Every failure write site now also stores the structured `reason_code` it already had:

- `PublishError.reason_code`
- new `PublishTikTokError.reason_code` values for local preconditions
- the platform's own `fail_reason` code
- reconciliation's terminal errors

`publishing/failure_taxonomy.py` maps codes to fixed categories, copy and action hints, using
`publishing/tiktok/failure_codes.py` for TikTok-only codes. Unknown and pre-3.11 (NULL) codes
are `UNKNOWN_ERROR`. Postgres migration `0009` adds the column (additive); SQLite adds it on
store open.

Action hints are advisory only (`RECONNECT_ACCOUNT`, `EDIT_CAPTION` (dropped once the caption is
locked), `TRY_AGAIN_LATER`). No retry endpoint or automatic recovery was added; that is
Milestone 3.13.

## Addendum (2026-10-03, Milestone 3.14 final follow-up): Library publishing status

The Library shows the same status as the Queue rather than its own guess. `GET /api/videos`
returns `publish_status`, from `publish_status.resolve_video_publish_status`: `UNSCHEDULED` when
`videos.assigned_slot_id` is NULL, otherwise exactly `resolve_slot_publish_status` for that slot
(a slot that doesn't point back at the video resolves to `NEEDS_ATTENTION`/`STATE_INCONSISTENT`).
This works because `assigned_slot_id` stays set after publishing (`content_slots.status` never
goes past `ASSIGNED`, per this ADR). No schema change.

Library tabs bucket that value: Unscheduled, Published, and Scheduled for everything else
(Scheduled, Publishing, Failed, Needs attention). The badge uses the Queue's labels.

Open for multi-platform (Milestone 4): the video status inherits the slot's most-urgent rule, so
a video published on TikTok but failed on another platform shows Failed and sits under
Scheduled. Whether the Library should show per-platform status, or treat "published anywhere" as
Published, is undecided. Listing also does two reads per scheduled video (N+1). That's fine at
current library sizes; batch it if libraries get large.
