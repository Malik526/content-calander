# Milestone 3.1 — Hosted Architecture Boundary

Architecture/boundary-definition evidence — documentation-only milestone, no runtime code
changes. Recorded 2026-09-19.

## Objective

Answer, before any hosted infrastructure is added: which parts of the current local system
become API-accessible application logic, which remain background-job logic, and which become
infrastructure concerns once Pickle Batch moves from a local tool to a hosted product. Produce
a concrete, code-grounded target architecture for Milestones 3.2–3.14 — not build any of it.

## Phase 1 — Guidance Read First

Read root `AGENTS.md` in full before investigating. Confirmed its "Current Product Stage"
section already states the correct starting point ("Milestone 3.0 ... complete. Next up:
Milestone 3.1") and its Scope Guardrails section already lists every guardrail this milestone's
brief also states (no FastAPI, no Postgres/Supabase, no user auth/ownership, no object storage,
no hosted/cron scheduler, no new publishing platforms/TikTok behavior, no schema redesign, no
`web/` changes). Also read `PROJECT_STATE.md` (current architecture/behavior narrative) and
`docs/evaluations/productization/milestone-3.0-backend-package-refactor.md` (the immediately
prior milestone, which this one builds directly on) before touching anything else. No
conflicting or missing convention was found — the global policies and this project's own
`AGENTS.md` already define everything Phase 1 asked to confirm.

## Phase 2 — Runtime Surface Inventory

Read every file in `src/content_automation/` (5 packages, ~7,700 total lines across the
runtime package + CLI + eval tooling), plus all 11 `cli/*.py` entry points and `config.py`, in
full — not excerpts. Confirmed directly (not assumed from `docs/evaluations/productization/
milestone-3.0-...md`'s own claims):

- `grep -rl "^import sqlite3" src/ cli/ tools/` → exactly one match
  (`persistence/content_store.py`). `ContentStore` is the sole SQLite access point.
- Every `cli/*.py` file is argument parsing + construction + a call into the package — no
  branching/looping logic lives in a CLI file (spot-checked `cli/worker.py`,
  `cli/process_content.py`, cross-referenced against `PROJECT_STATE.md`'s own package-boundary
  description).
- `publish_tiktok.execute_claimed_platform_post`, `worker.run_due_posts_once`,
  `reconciliation.reconcile_pending_status_checks_once`,
  `crash_recovery.recover_stale_posts_once`, and `media.processing.process_one` all take plain
  parameters (`store`, `publisher`, optional `now`) and return a dataclass summary — no CLI
  coupling, no global state.
- No module outside `config.py` reads an environment variable directly — every setting flows
  through `config.py`'s single set of `os.getenv(...)` calls.

Full inventory (functions reusable by a future FastAPI route vs. CLI-only vs. background-only
vs. infrastructure-adapter vs. engineering-only) recorded in
`docs/architecture/hosted-product-boundary.md` §4/§5/§13 rather than duplicated here.

## Phase 3–16 — Target Architecture, Boundaries, and Decisions

Produced directly in `docs/architecture/hosted-product-boundary.md` (the canonical document
this milestone was asked to leave behind for 3.2–3.14). Summary of what each phase found:

- **Target layers (§2–3):** five layers (API, application/domain, domain/runtime logic,
  persistence/infra adapters, background jobs) — no DDD-style widening, matching the brief's
  explicit preference and this codebase's existing simplicity.
- **API boundary (§4):** every synchronous-candidate operation checked against the real
  implementation (not assumed) — e.g. confirmed `TikTokPublisher.publish()`'s
  `_UPLOAD_TIMEOUT_SECONDS = 300` and `media.inspection`'s subprocess timeouts (30s/300s)
  before classifying publishing/media-processing as must-be-async.
- **Background jobs (§5):** all four already one-pass, already idempotent by construction
  (traced each one's idempotency mechanism directly: content-hash keying, atomic claim,
  never-resubmit-once-`platform_post_id`-is-set, optimistic concurrency) — table built from
  the real function signatures and real docstrings, not invented.
- **Persistence boundary (§6):** confirmed `ContentStore` is already clean (Phase 2's grep);
  found the SQLite-specific behavior that exists (PRAGMAs, rebuild-based migrations) is fully
  contained inside `ContentStore` itself, never leaked upward — **decision: do not extract a
  repository/interface abstraction now**, per the brief's own stated preference against
  premature abstraction, since one already effectively exists. Clarified during review (before
  this milestone was treated as closed): `ContentStore` remaining *the* persistence
  abstraction through Milestones 3.2 (ownership) and 3.3 (Postgres) does not mean its current
  method signatures are frozen — user-owned queries/jobs will likely need explicit tenant
  scoping (`user_id`/`platform_connection_id`, a scoped store context, Postgres row-level
  security, or equivalent) added to methods that assume a single global tenant today. What
  must survive both migrations unchanged is the *scheduling and concurrency semantics*
  (atomic claim, optimistic concurrency), not today's exact parameter lists.
- **Multi-tenant execution invariant (§5, added during review):** made explicit, at the same
  review point, that a hosted background job must never operate on one user's records using
  another user's credentials — scheduled publishing, reconciliation, crash/stale recovery, and
  media processing must all eventually execute within explicit ownership/account scope. Not
  enforced today (single-tenant, no second account to violate it against), but recorded now as
  the acceptance bar any Milestone 3.2+ job redesign must be checked against.
- **Media/storage boundary (§7):** every local-filesystem call site enumerated directly (grep
  for `Path(`/`shutil`/`.open(` patterns across `media/` and `publishing/tiktok/`), not
  inferred from module docstrings alone.
- **Credential boundary (§8):** every global-single-credential-file assumption enumerated
  (`TIKTOK_TOKEN_PATH`, `CALENDAR_OAUTH_TOKEN_PATH`, `APP_CALENDAR_STATE_PATH`,
  `TIKTOK_REFRESH_LOCK_PATH`), each traced to its one real call site.
- **Configuration classification (§9):** every setting in `config.py` (all ~90 lines of actual
  values, not the comments) sorted into one of five categories by re-reading the file in full.
- **User-ownership impact map (§10):** built from the same inventory — no DB schema was read
  or altered to produce it, only the existing table/column list already known from
  `content_store.py`'s schema constants.
- **Flows (§11):** five flows, all conceptual, each traced against the real function that
  would participate (not invented names).
- **API placement (§12):** `src/content_automation/api/`, matching the existing
  `src/content_automation/<package>/` convention rather than a new layout. No package created.
- **Service layer (§13):** re-checked each of the brief's five named examples (upload/process
  video, schedule video, reschedule post, connect platform, get queue) against the real
  codebase — four already exist as callable package functions; one genuine gap found
  (reschedule) and recorded, not filled.
- **Infrastructure decision matrix (§14) and provider deferrals (§16):** every row's "decision
  needed now?" answered `No` except frontend hosting, which was already decided (Netlify,
  pre-existing `netlify.toml`) and is out of this milestone's backend scope.
- **Migration risk list (§15):** ranked must-fix / milestone-specific / safe-to-defer, each
  entry tied to a specific file/line-level finding from Phases 2 and 6–9, not a generic
  hosted-migration checklist.

## Phase 17 — Code Changes

**None.** Investigated whether any current coupling makes a future boundary unnecessarily
difficult to establish, per the brief's own bar ("small, behavior-preserving, well-tested,
clearly justified"). Found none: `ContentStore` is already the sole persistence boundary,
`Publisher` is already the sole publishing boundary, the CLI is already thin (Milestone 3.0),
and every background operation is already a plain one-pass function. Nothing investigated in
Phases 2–16 required a code change to document correctly. Per the brief's own acceptance
criterion ("If no code changes are necessary, that is a valid and potentially preferable
result"), this milestone ships as documentation-only.

No ADR was created. Per `AGENTS.md` ("Most milestone work is *not* an ADR; do not create one
unless the work genuinely changes an architectural direction") and the same reasoning Milestone
3.0 itself used for skipping an ADR: the target shape this milestone documents was the
milestone brief's own stated goal, not an architectural direction this investigation arrived at
independently. The architecture document itself is the canonical record.

## Phase 18 — Tests / Validation

No runtime code changed, so no new behavioral tests were required or added. Ran the full suite
to confirm the baseline is unchanged and that nothing in this investigation (including the
`grep`-based verification passes above) had any side effect:

```text
python3 -m pytest -q
596 passed, 1 warning in 16.07s
```

596 passed — matches the baseline `AGENTS.md`/`CHANGELOG.md` record from Milestone 3.0 exactly
(unchanged, as expected for a documentation-only milestone). The one warning is a pre-existing,
unrelated `google.api_core` Python-version deprecation notice, not something this milestone
introduced.

No live TikTok API calls were made. No real Google Calendar writes were made.

## Real Environment Guardrail

Read `data/content.db` directly and read-only (`sqlite3 -readonly`, not `ContentStore()`, to
guarantee no write path could execute) before writing any document:

```text
videos:          5 rows, all status=ASSIGNED
platform_posts:  id=1 FAILED (no platform_post_id)
                 id=3 PUBLISHED (platform_post_id=v_pub_file~v2-...)
                 id=4 PUBLISHED (platform_post_id=v_pub_file~v2-...)
                 id=5 PENDING
                 id=6 PENDING
```

No row was written, updated, or deleted by this milestone's work. No schema migration ran (none
was introduced — `_VIDEOS_MIGRATION_COLUMNS`/`_PLATFORM_POSTS_MIGRATION_COLUMNS` untouched).

## Documents Produced

- `docs/architecture/hosted-product-boundary.md` — the canonical target-architecture reference
  for Milestones 3.2–3.14 (current architecture, target layers, API/background-job boundary,
  persistence/media/credential/config boundaries, user-ownership map, target flows, future API
  placement, service-layer findings, infrastructure decision matrix, migration risk list,
  deferred provider decisions).
- This evaluation record.

`PROJECT_STATE.md` was not updated — nothing in it became inaccurate (no runtime behavior
changed). `README.md` was not updated for the same reason. `AGENTS.md`'s "Current Product
Stage" line is updated separately (see `CHANGELOG.md` entry for this milestone) to point past
3.1 at 3.2, mirroring how Milestone 3.0 updated the same line for itself.

## Conclusion

Investigated the full post-3.0 package/CLI surface (~7,700 lines) directly rather than assuming
3.0's own summary was still accurate, and confirmed it was: `ContentStore` is the sole
persistence boundary, `Publisher` is the sole publishing boundary, every background operation
is already a one-pass function, and the CLI carries no logic of its own. Documented the parts
that were genuinely undefined before this milestone — the API/background-job split, the
media/storage contract, the credential/user-ownership model, configuration classification, a
ranked migration-risk list, and (added during review, before this milestone was treated as
closed) the multi-tenant execution invariant every future background job must satisfy and the
explicit statement that `ContentStore`'s method signatures are not frozen through the 3.2
(ownership)/3.3 (Postgres) migrations even though `ContentStore` itself remains the
persistence abstraction — in `docs/architecture/hosted-product-boundary.md`. No code
changes were required or made; the full test suite (596) passed unchanged; the real database
was read only, never mutated; no live TikTok or Google Calendar calls were made.

**Milestone 3.1: COMPLETE.**
