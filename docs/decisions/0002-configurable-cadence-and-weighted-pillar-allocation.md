# ADR-0002: Configurable Posting Cadence and Weighted Pillar Allocation

## Status

Accepted

## Context

`generate_calendar.py` originally decided both *when* to post and *what pillar* to post through one fixed table (`WEEKLY_SCHEDULE`: weekday name -> pillar key), plus a special case (`FIFTH_SUNDAY_CONTENT_TYPE`) to rebalance the month when the weekly mapping alone couldn't hit the target percentages. That worked for one creator's fixed 7-posts/week personal schedule, but doesn't generalize: a different posting cadence, a different set of posting days, or a different number of pillars all required editing the mapping by hand, and the fifth-Sunday hack was really a symptom of allocation being encoded through weekday instead of computed directly.

## Decision

Split the decision into two independent, separately testable questions, both in the new `scheduling.py` (stdlib only, no dependency on Google Calendar or the pipeline database):

- **WHEN — `generate_posting_dates()`.** Driven by `config.POSTS_PER_WEEK` (1-7), `config.POSTING_DAYS` (`"auto"` or an explicit weekday list), and `config.POSTING_TIME`. Slot count for a month is derived from real calendar dates (how many of the chosen weekdays actually fall in that month), never `posts_per_week * 4`. Auto mode (`auto_posting_weekdays`) picks evenly-spaced weekday indices (`round(i * 7 / n) % 7`) — deterministic, no LLM, no randomization; explicit days always override it.
- **WHAT — `allocate_pillars()` + `distribute_pillars()`.** `allocate_pillars` uses the largest-remainder method to turn arbitrary per-pillar weights (`config.CONTENT_TYPES[key]["weight"]`, replacing `target_percent`) into integer counts that sum exactly to the slot count, for any number of pillars (not just four) — this is the single mechanism for monthly pillar counts now, so `FIFTH_SUNDAY_CONTENT_TYPE` and the weekday-driven rebalancing it existed for are retired outright rather than kept as a parallel special case. `distribute_pillars` then orders those counts across the month using smooth weighted round-robin (the algorithm nginx uses for weighted load balancing) so pillars interleave instead of clustering, while still landing on the exact counts `allocate_pillars` computed.
- **Prompts are optional metadata, not routing input.** `generate_calendar.build_schedule()` calls `generate_posting_dates()` and `allocate_pillars()`/`distribute_pillars()` first, and only *then* attaches a prompt (rotated per pillar, same sequential/wrap rule as before) if `config.PROMPT_GENERATION_ENABLED`. Slot creation never requires a prompt; `content_slots.prompt` is nullable.
- **`content_slots` uniqueness moves to `scheduled_at` alone.** Under the old model, `(scheduled_at, pillar_key)` was unique because one weekday reliably implied one pillar. Under a weighted allocator, a changed strategy could compute a *different* pillar for an existing timestamp — so the invariant that actually matters is "one posting datetime, one slot," full stop. `content_store.py` migrates any database still on the old constraint the first time it's opened (`_migrate_content_slots_unique_constraint`): it rebuilds the table, and where the old constraint allowed two rows to share a timestamp (only possible via different `pillar_key`s), keeps the row with the most-advanced status (`PUBLISHED` > `ASSIGNED` > `OPEN`/`FAILED`) so an already-assigned slot is never displaced by a still-open duplicate.
- **Regeneration is additive, not reconciling.** `insert_slot_if_missing` already skips a `scheduled_at` that exists; that single behavior is now also what protects against a changed config silently rewriting an existing slot's pillar. Re-running `generate_calendar.py` after changing `POSTS_PER_WEEK`/`POSTING_DAYS`/`CONTENT_TYPES` adds slots for any newly-covered timestamp and leaves every previously-persisted slot exactly as it was — it does not reconcile the whole month to the new strategy. A full reconciliation engine (diff old vs. new strategy, decide what's safe to move) is explicitly deferred; mixing two strategies within one already-generated month is a known, documented limitation of this milestone, not a bug.

## Rationale

Keeping date generation and pillar allocation as two pure functions (`scheduling.py` has no I/O) makes each one trivially testable in isolation — a scheduling bug is always traceable to either "wrong dates" or "wrong counts/order," never an entangled monthly loop. Largest-remainder is the standard method for turning percentages into integer counts that sum exactly to a target; smooth weighted round-robin is a well-known, small, non-random algorithm for exact-count interleaving, which was preferred here over a bespoke optimizer per the instruction to keep this small and understandable.

## Consequences

- `WEEKLY_SCHEDULE` and `FIFTH_SUNDAY_CONTENT_TYPE` no longer exist in `config.py`. Any doc or script referencing them is stale.
- `CONTENT_TYPES` entries use `"weight"`, not `"target_percent"`.
- `generate_calendar.ScheduledPost` now carries a `scheduled_at: datetime` (not `day: date`) and `prompt: str | None`.
- A database created before this migration is upgraded automatically and non-destructively the first time it's opened; the migration prints a one-line note to stderr only if it actually had to consolidate duplicate-timestamp rows.
- Mixing strategies mid-month (change config, regenerate the same month) is additive-only in this milestone — see "Regeneration is additive, not reconciling" above. A reconciliation engine is future work, not built here.

## Guardrails

- Do not let `scheduling.py` import `content_store.py`, `googleapiclient`, or anything Google-Calendar-specific — it stays pure date/allocation math.
- Do not reintroduce a weekday -> pillar special case; if a rebalancing need reappears, it belongs in `allocate_pillars`/`distribute_pillars`, not a new per-weekday exception.
- Do not have `process_content.py` or `slot_matcher.py` read `content_slots.prompt` for any routing decision — it is display/description metadata only.
- Do not silently overwrite an existing `content_slots` row's `pillar_key`/`prompt` on regeneration; `insert_slot_if_missing`'s ignore-on-conflict behavior is load-bearing for this guarantee.

## Current Implementation

- `scheduling.py` — `generate_posting_dates`, `auto_posting_weekdays`, `resolve_posting_weekdays`, `parse_posting_time`, `allocate_pillars`, `distribute_pillars`, `validate_schedule_config`.
- `config.py` — `POSTS_PER_WEEK`, `POSTING_DAYS`, `POSTING_TIME`, `PROMPT_GENERATION_ENABLED`, `CONTENT_TYPES[*]["weight"]`.
- `generate_calendar.py` — `build_schedule(year, month)` composes `scheduling.py`; CLI/dry-run/Google Calendar push otherwise unchanged.
- `content_store.py` — `SCHEMA_CONTENT_SLOTS` (`UNIQUE(scheduled_at)`, nullable `prompt`), `_migrate_content_slots_unique_constraint`.
- `tests/test_scheduling.py`, `tests/test_generate_calendar.py`, `tests/test_content_store.py` (migration test).
