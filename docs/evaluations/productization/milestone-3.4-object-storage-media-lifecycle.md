# Milestone 3.4 — Object Storage + Media Lifecycle

Implementation and validation evidence. Recorded 2026-09-20.

## Objective

Remove the hosted publishing and hosted processing paths' dependency on permanent local media
files: durable tenant-scoped object storage, a small storage contract, materialization-on-demand
for `ffprobe`/`ffmpeg`/TikTok's `FILE_UPLOAD`, a first-class hosted processing entry point, and a
real migration path for existing local media — without redesigning TikTok publishing/retry/
reconciliation/scheduling/ownership semantics, building deletion/retention machinery prematurely,
building the upload UI, or removing local CLI ingestion, which remains fully supported and
permanent-local-file for local development.

**Correction (during review, before this milestone was treated as closed):** the first pass of
this milestone left this Objective unqualified ("remove Pickle Batch's remaining dependency on
permanent local media files") and left `media/processing.py`'s ingestion path — the *only*
processing path at the time — completely untouched, which was a real architecture gap: a
storage-backed video (uploaded but never locally processed) had no way to be processed without
first being forced through a permanent local round trip. See Phase 10 below for the fix and
ADR-0009's "Media processing" section for the full correction record.

## Phase 1 — Guidance Read First

Read `AGENTS.md`, `docs/architecture/hosted-product-boundary.md`, `docs/decisions/
0007-user-ownership-model.md`, `docs/decisions/0008-postgres-persistence-migration.md`, and
`docs/evaluations/productization/milestone-3.3-postgres-migration.md` in full before writing any
code. Confirmed the starting architecture: SQLite and Postgres persistence both exist; Postgres
is the proven hosted backend (real production data already migrated there); user ownership
exists and is enforced; object storage was intentionally still deferred; `canonical_media_path`/
`original_path` currently hold local path strings.

## Phase 2 — Media Filesystem Assumption Inventory

Inspected `media/inspection.py`, `media/processing.py`, `publishing/tiktok/publisher.py`,
`persistence/content_store.py`, `config.py`, and the relevant CLI entry points for every local-
filesystem assumption, categorized:

- **Permanent-storage assumption:** `videos.canonical_media_path`/`original_path` (both
  columns), `config.CONTENT_DIR`/`INCOMING_DIR`/`PROCESSED_DIR`/`FAILED_DIR`.
- **Temporary-working-file assumption:** `media.inspection.extract_audio`'s WAV derivative
  (already temp-file-based and already cleaned up pre-3.4 — no change needed).
- **Input/discovery assumption:** `media.processing.discover_videos` (`content/incoming/`).
- **Output/cleanup assumption:** `media.processing._move_file` (`shutil.move` between local
  dirs), `publishing/tiktok/publisher.py`'s `video_path.open("rb")`.

This inventory directly informed the scoping decision in Phase 10/11 below: none of the
ingestion-side assumptions needed to change; only the *publishing*-side read needed a new,
additive resolution step.

## Phase 3 — Object Storage Provider

Supabase Storage — evaluated against the brief's own criteria (durable, private, user-scoped
naming, Python-backend compatibility, cost, large-video support, operational simplicity), not
selected merely for stack consistency with Milestone 3.3's Postgres choice, even though they
share a project. Implemented via plain `requests` against Supabase Storage's REST API (already
a project dependency; no new SDK added) — every API shape (`POST` upload with `x-upsert`,
`HEAD` existence check, streamed `GET` download, `DELETE` with a `{"prefixes": [...]}` body) was
verified directly against a real Supabase project before any production code was written, not
assumed from documentation. Two private buckets created via the Storage API itself (not the
dashboard): `pickle-batch-media` (real) and `pickle-batch-test` (disposable, test-only).
**Choosing Supabase Storage did not select Supabase Auth** — nothing about authentication was
touched. Full reasoning in ADR-0009.

## Phase 4 — Storage Contract

`storage.protocol.StorageProtocol` — `put`, `exists`, `delete`, `materialize` (a context manager
yielding a real local `Path`, cleaned up on exit). A `typing.Protocol`, mirroring
`persistence.protocol.ContentStoreProtocol`'s exact structural-typing shape from Milestone 3.3 —
no inheritance imposed on either implementation.

## Phase 5 — Local Storage Preserved

`storage.local.LocalStorage` — filesystem-backed, `config.LOCAL_STORAGE_ROOT` default, the
dev/test backend. Nothing in the existing test suite requires network storage; `LocalStorage` is
what every fast unit test (34 of the 42 new tests) exercises.

## Phase 6 — Canonical Media Reference

Decision B (per the brief's own framing): two new columns, `storage_provider`/`storage_key`, on
`videos` — both nullable at the SQL level under both backends (a deliberate divergence from
Milestone 3.3's `user_id` decision — see ADR-0009 "Canonical Media Reference" for why: no real
row has ever had a value to backfill, so `NOT NULL` had nothing to apply to yet).
`canonical_media_path`/`original_path` are untouched in schema and, for any video with
`storage_provider IS NULL`, untouched in meaning too — verified by the full pre-existing test
suite passing with zero modifications.

## Phase 7 — Object Key Design

`users/<user_id>/videos/<video_id>/source<ext>` (`media.media_storage.build_storage_key`) —
deterministic (not a random UUID), tenant-scoped, unique per video, no user-provided filename or
secret information embedded. Verified directly:
`test_build_storage_key_is_tenant_scoped_and_deterministic`.

## Phase 8 — Private-by-Default Media

Both buckets created with `public: false`. Every access uses the service-role key
(server-side only — `config.SUPABASE_SERVICE_ROLE_KEY`, read from the `.env` variable literally
named `SERVICE_ROLE_KEY`); no client-side/browser credential exists or was exposed. No signed
upload/download URL scheme was built — out of scope until a real client (web/mobile) needs one.

## Phase 9 — Materialization Boundary

`StorageProtocol.materialize()` — a context manager, `with storage.materialize(key) as path:`,
exactly the shape the brief asked for. Both implementations clean up their temp file on exit,
success or exception — verified directly for both:
`test_materialize_cleans_up_temp_file_on_success`/`_on_exception` (`LocalStorage`), and the
`SupabaseStorage` round-trip test's own cleanup assertion. No permanent copy is ever retained on
the worker filesystem.

## Phase 10 — Media Processing: Ingestion Unchanged, a Real Hosted Entry Point Added on Review

**Original pass:** `media/processing.py`'s ingestion pipeline left completely unchanged —
verified by the full pre-existing FIFO/pillar-mode test suite passing with zero modifications.
Uploading an already-processed local video to object storage was a separate, additive step
(`media.media_storage.upload_canonical_media`), not a rewrite of `process_one`.

**Gap found on review, before this milestone was treated as closed:** leaving
`media/processing.py`/`discover_videos`/`_move_file` as the *only* processing path meant a
storage-backed video (uploaded but never locally processed — the shape a future upload API would
actually produce) had no way to be inspected/transcribed/captioned/scheduled without first being
forced through a permanent `content/incoming/`→`content/processed/` round trip. This directly
contradicted the milestone's own "local files are only ever temporary materializations" framing,
which was accurate for the *publishing* path but not yet true for *processing*.

**Fix:** `process_one` gained two optional hooks — `on_failed: Callable[[Path], None]`,
`on_assigned: Callable[[Path], Path | None]` — defaulting to the exact pre-3.4
`_move_file(path, FAILED_DIR)`/`_move_file(path, PROCESSED_DIR)` behavior, so every existing
caller (including `cli/process_content.py`) is unaffected when it doesn't pass them — verified
by re-running the full suite immediately after this specific change (688 passed, unchanged from
before the fix). A new `media.processing.process_storage_backed_video(store, storage,
transcriber, classifier, video_id, user_id, ...)` reuses `process_one`'s inspection/
transcription/caption/scheduling logic verbatim (not a duplicated pipeline) by materializing the
video from object storage into a real temp path
(`media.media_storage.materialize_canonical_media`) and calling `process_one` against it with
no-op hooks — nothing is moved anywhere; the temp materialization's own context manager cleans
it up on the way out, success or exception. The same content-hash identity (`file_hash`)
`process_one` already relies on for idempotency correctly resolves the already-inserted `videos`
row rather than creating a duplicate.

This was a deliberate, bounded scope decision even after the fix: rewiring the entire local
ingestion pipeline to *require* object storage was never in scope and still isn't — local CLI
ingestion remains fully supported, unchanged, and permanent-local-file, exactly as the review
feedback that caught this gap explicitly asked for ("do not rewrite or remove the existing local
ingestion workflow; it should remain supported for local development"). See ADR-0009's "Media
processing" section for the full correction record, and
`tests/test_process_storage_backed_video.py` for the focused tests proving: a storage-backed
video can be processed with its original local source absent; the temporary source is removed on
success; the temporary source is removed on processing failure; ownership is checked (via a spy
storage implementation that raises if `materialize()` is reached before the check) before storage
access; and existing local `process_one` behavior is unchanged by the new hooks' defaults.

## Phase 11 — TikTok Publisher: Not Modified

Per the brief's own explicit preference. `scheduling/publish_tiktok.py`'s
`execute_claimed_platform_post`/`publish_video` gained an optional `storage` parameter and a new
shared helper (`_resolved_media_path`, a context manager) that yields either
`video.canonical_media_path` directly (legacy) or a real materialized temp path (storage-backed)
— `_validate_ready_to_publish` and the actual `publisher.publish()` call both operate on
whichever `Path` they're handed, with no idea which case applies. `TikTokPublisher` itself was
not touched at all — confirmed by `git diff` scope. `worker.run_due_posts_once` gained the same
optional `storage` parameter, forwarded unchanged.

**A real refactor risk was here, and it was tested immediately.** This is the highest-risk
change of the milestone (`publish_tiktok.py` has the largest pre-existing test surface in this
codebase). The full 646-test suite (pre-3.4 baseline) was re-run immediately after this specific
refactor, before any new capability was added on top of it, and passed unchanged — confirming
the legacy branch is byte-for-byte behaviorally identical to before.

## Phase 12 — Upload/Ingestion Boundary

`media.media_storage.upload_canonical_media(store, storage, video_id, user_id)` — the backend
ingestion path the brief asks for, reusable by both the migration tool (Phase 21) and any future
upload-time hook. No new lifecycle states were added (per the brief's own "do not add states
speculatively" instruction) — a video's existing `videos.status` lifecycle is untouched;
`storage_provider IS NULL` vs. set is itself the only new state distinction needed, and it is
already exactly representable by the two new nullable columns.

## Phase 13 — Upload Idempotency

The deterministic key format (Phase 7) makes re-uploading the same video naturally idempotent —
`put()`'s upsert contract simply overwrites the same key rather than creating a duplicate.
Verified: `test_upload_canonical_media_is_idempotent`. Deduplication is scoped within a single
video's own identity (`file_hash` already unique per Milestone 1's schema) — no cross-user
deduplication was implemented or considered; two different users' identical video content
produces two separate objects under two separate tenant-scoped keys, never one shared object.

## Phase 14 — Media Metadata

No new metadata columns beyond `storage_provider`/`storage_key` were added — every other field
the brief lists (content type, bytes, sha256, duration, width, height, container, codecs) was
already correctly represented on `videos` from earlier milestones; duplicating it would have
been exactly the "do not duplicate metadata already correctly represented elsewhere" the brief
warns against.

## Phase 15/16/17 — Lifecycle, Retention, Deletion Safety

**Explicitly deferred, per the brief's own allowance.** No deletion of canonical media (local or
object-storage) was implemented for either backend. `StorageProtocol.delete()` exists (needed
for disposable test-object cleanup and interface completeness) and is tested directly, but no
production code path calls it against real canonical media. Retention policy: keep every source
indefinitely until multi-platform publishing exists to make "all intended platforms terminal" an
evaluable condition — documented explicitly in ADR-0009, not left as a silent gap.

## Phase 18 — Failure Semantics

`storage.supabase_storage.StorageError` mirrors `publisher.PublishError`'s structured shape
(`reason_code`, `http_status`) — `NETWORK_ERROR` for a transport-level failure, `HTTP_ERROR` for
an unexpected status, `NOT_CONFIGURED` for a missing service-role key — the same "structured,
not string-parsed" pattern this codebase already established for TikTok/auth failures.
`materialize()` raises the shared `StorageObjectNotFoundError` (defined once, in `storage.local`,
imported by `storage.supabase_storage` — not two independently-defined "not found" exceptions)
for a missing object, distinct from a transport/HTTP failure — callers can tell "this object
genuinely doesn't exist" from "storage was unreachable" without string-matching a message.

## Phase 19 — Tenant Isolation

Proven at the application/storage boundary, not left to rely on object-key obscurity:
`media.media_storage.upload_canonical_media`/`materialize_canonical_media` both resolve and
verify the video's real owner against the caller-supplied `user_id` *before* touching storage at
all — `MediaOwnershipError` otherwise. Verified directly against both backends:
`test_upload_canonical_media_rejects_wrong_user`/
`test_materialize_canonical_media_rejects_wrong_user` (LocalStorage, fast) and the equivalent
manual proof run against real Supabase Storage during development (a known, real storage key,
requested under the wrong `user_id`, correctly refused before any HTTP call was made).

## Phase 20 — Postgres Integration

`persistence/postgres_migrations/0002_add_media_storage_columns.sql` — a new forward migration,
**not** a rewrite of `0001_initial_schema.sql` (already applied against real production data).
Verified: `test_migrations_are_idempotent_on_reopen` (updated to compare against the real
migrations-directory file count rather than a hardcoded `1`, since it now legitimately needs to
be `2` — the hardcoded version would have gone stale exactly the way this update caught).
Reopening a store against an already-migrated schema applies nothing new; existing rows,
ownership, and publishing state all survive migration application untouched (verified by the
full existing Postgres integration suite continuing to pass unchanged after `0002` was added).

## Phase 21 — Existing Media Migration

`cli/migrate_media_to_object_storage.py` — for every video with a local `canonical_media_path`
and no `storage_provider` yet: compute the local file's SHA-256, upload via
`upload_canonical_media`, materialize the just-uploaded object back down and recompute its
SHA-256, compare. Preserves video IDs, statuses, `platform_posts` state, transcripts/captions
(none of these are touched — only `storage_provider`/`storage_key` are written). Makes zero
TikTok API calls (verified by inspection — the script never imports `publishing.tiktok.*`).
**Never deletes the original local file** — verified directly:
`test_migrates_video_with_local_file` asserts the source path still exists after migration.

## Phase 22 — Migration Idempotency

`test_skips_already_migrated_video` — a second run reports `skipped_already_migrated` and
performs zero uploads. `test_skips_video_whose_local_file_no_longer_exists` and
`test_skips_video_with_no_canonical_media_path` cover the "source missing"/"nothing to migrate
yet" cases explicitly, each reported with a distinct, clear outcome rather than a generic
failure.

## Phase 23 — Verification

Every real migration (rehearsal and the real run — see "Real Data" below) verifies source exists,
destination exists (implied by successful materialization), byte size and SHA-256 match, and the
DB `storage_key` matches what was actually written — machine-checkable, not eyeballed: the
migration script's own `hash_mismatch` outcome would have caught and reported any divergence, and
none occurred.

## Phase 24 — Storage Configuration

`config.STORAGE_BACKEND` (`"local"`/`"supabase"`), `config.LOCAL_STORAGE_ROOT`,
`config.SUPABASE_URL` (reused from Milestone 3.3), `config.SUPABASE_SERVICE_ROLE_KEY` (reads the
`.env` variable `SERVICE_ROLE_KEY`), `config.SUPABASE_STORAGE_BUCKET`/`_TEST_BUCKET`. The
service-role key is never logged, never printed, never committed — every diagnostic script
written during this milestone's setup redacted it explicitly (see "Storage Provider" phase); `git
grep` confirms it appears nowhere in any tracked file.

## Phase 25 — Storage Factory

`storage.factory.build_storage()` — `"local"` → `LocalStorage`; `"supabase"` → `SupabaseStorage`;
a configured hosted backend with missing credentials raises immediately
(`SupabaseStorage.__init__`'s own check) rather than silently falling back to `LocalStorage`.
Verified directly: `test_supabase_backend_without_credentials_raises_not_silently_falls_back`.
Mirrors `persistence.store_factory.build_content_store()`'s exact philosophy from Milestone 3.3.

## Phase 26 — Temporary Files

`tempfile.NamedTemporaryFile` throughout (both backends), OS-assigned unique filenames, cleaned
up in a `finally` block — verified for both success and exception paths (Phase 9 above). No
canonical DB reference ever points at a temp path.

## Phase 27 — Large File Behavior

Uploads stream from an open file handle (`requests.post(..., data=open(path, "rb"))`); downloads
stream via `iter_content` directly to disk. Verified with a real 5MB transfer against Supabase,
not only tiny fixtures: `test_large_ish_file_streams_without_loading_whole_object_into_memory`.

## Phase 28 — Concurrency / Duplicate Processing

No distributed lock introduced. The deterministic storage key already makes concurrent/repeated
upload attempts for the same video safe (upsert, not duplicate); `ContentStore`'s existing
per-video/per-`(video_id, platform)` row semantics already prevent duplicate publishing work, as
they did before this milestone. Documented, not re-engineered, per the brief's own "do not
create an expensive locking subsystem unless evidence requires one."

## Phase 29 — End-to-End Hosted-Media Simulation

`tests/test_media_storage_postgres.py` — real Postgres + real Supabase Storage + `FakePublisher`,
through the actual production modules (`slot_matcher`, `platform_post_materializer`, `worker`,
`media_storage`), not reimplemented test logic. Two tests:

- `test_upload_to_published_against_real_postgres_and_real_object_storage` — the original,
  narrower version: insert a video that's already effectively "prepared" (transcript/caption
  manually stamped), upload its media to real object storage, delete the local original, assign a
  slot, materialize the `platform_posts` row, run the real worker (which materializes the video
  from real object storage into a real temp file), `FakePublisher` publishes, asserts
  `PUBLISHED`, asserts the temp materialization was cleaned up afterward.
- `test_upload_through_processing_to_published_against_real_postgres_and_real_object_storage`
  (added on review, per the same feedback that caught Phase 10's gap) — the fuller version: starts
  from a real ffmpeg-synthesized video that has **never** been locally processed (uploaded to
  real object storage as-is, no `canonical_media_path` ever recorded), runs it through the real
  hosted processing entry point (`media.processing.process_storage_backed_video` — real `ffprobe`
  inspection, a fake transcriber only to avoid a real Whisper model download in a test, real
  caption derivation, real FIFO slot assignment against real Postgres, real object-storage
  materialize/cleanup), and only then continues into the worker/publish stage — proving the whole
  hosted pipeline end to end, upload through publish, not just its back half. A real test-timing
  bug was found and fixed while writing this: `process_storage_backed_video`'s FIFO slot matching
  has no injectable `now` (unlike `worker.run_due_posts_once`) and always uses real wall-clock
  time, so the seeded slot had to be a real near-future time, not relative to this file's other
  tests' fixed fictional `NOW` — fixed by computing the real "now" via
  `slot_matcher.now_in_config_timezone()` for this specific test.

No live TikTok call in either. Both passed on the first real run (after the timing fix) against
both live services simultaneously.

## Phase 30 — Real Storage Validation

Performed as part of building `storage.supabase_storage.SupabaseStorage` itself, before any
production code depended on it: a real disposable-key round trip (put → exists → materialize →
verify bytes → delete → verify gone) against `pickle-batch-test`, run manually during development
and then formalized as `tests/test_storage_supabase.py::test_put_exists_materialize_delete_round_trip`
— no destructive operation ever targeted the real media bucket during this validation phase.

## Phase 31 — Current Real Media Guardrail

Before migrating any real video: read real `videos` rows read-only via
`PostgresContentStore` (id, user_id, `canonical_media_path`, `storage_provider`) and confirmed,
by direct filesystem check, all 5 real local files still exist (`content/processed/*.mp4`, 56MB
total) and none had a `storage_provider` set yet. A dry run
(`STORAGE_BACKEND=supabase python3 cli/migrate_media_to_object_storage.py --dry-run`) was run
first and matched this exactly (`would_migrate: 5`) before the real, non-dry-run migration —
which ran only after explicit user confirmation, per this repository's own guardrail against
mutating/uploading real business data outside an explicitly-confirmed, explicitly-scoped step.

## Tests

50 new tests. 41 run unconditionally, no network dependency (`tests/test_storage_local.py` ×9,
`tests/test_storage_factory.py` ×3, `tests/test_media_storage.py` ×9,
`tests/test_publish_tiktok_storage.py` ×6, `tests/test_migrate_media_to_object_storage.py` ×7,
`tests/test_process_storage_backed_video.py` ×7 — added on review, see Phase 10). 9 require real
credentials and are skipped automatically otherwise — verified with a real `DATABASE_URL=""
SERVICE_ROLE_KEY=""` run (9 skipped, 0 passed, 0 failed): `tests/test_storage_supabase.py` (×7,
needs `SUPABASE_SERVICE_ROLE_KEY`) and `tests/test_media_storage_postgres.py` (×2 — the original
publish-only end-to-end test plus the fuller upload-through-processing one added on review, both
needing `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`).

```text
python3 -m pytest -q
696 passed, 1 warning in 49.94s
```

696 = 646 baseline (Milestone 3.3) + 50 new. Zero existing tests modified except one legitimate,
expected update: `test_migrations_are_idempotent_on_reopen`'s hardcoded migration count (was `1`,
correctly became dynamic rather than hardcoded `2`, since this milestone added a second real
migration file — the exact staleness that hardcoded assertion existed to eventually hit).

## Real Data

- Real video files (`content/processed/*.mp4`, 5 files, 56MB total): **retained, unmodified** —
  this milestone's migration tool never deletes or writes to them.
- Real Postgres `videos` rows (`public` schema): `storage_provider='supabase'` and a real
  `storage_key` stamped on all 5, via the real (non-dry-run) migration run with explicit user
  confirmation. `canonical_media_path`, `status`, and every other field verified unchanged
  before/after.
- Real Supabase Storage `pickle-batch-media` bucket: 5 objects
  (`users/1/videos/{1..5}/source.mp4`), each verified present via a direct `exists()` check after
  the migration.
- Zero hash mismatches reported by the real migration run.

## External Effects

- TikTok API calls: zero.
- Real media uploaded: yes, to Supabase Storage, with explicit user confirmation obtained first
  (dry run shown, size disclosed, backend disclosed).
- Real media deleted: none, anywhere.
- Credentials exposed: none — `SERVICE_ROLE_KEY` never printed/logged by any script this
  milestone wrote; confirmed via `git grep` that it appears in no tracked file.

## Documentation

- Architecture: `docs/architecture/hosted-product-boundary.md` (updated in place — §7, §14,
  §16, §17, plus the top-of-file status callout).
- Evaluation: this record.
- ADR: `docs/decisions/0009-object-storage-media-lifecycle.md`.
- `.env.example`, `AGENTS.md`, `CHANGELOG.md`, `.gitignore` (added `data/media_storage/`) updated
  where genuinely affected.

## Guardrails Respected

No full batch-upload frontend, Google/Apple login, production FastAPI CRUD surface, hosted
worker/scheduler provider, Instagram/YouTube publishing, billing, native app, analytics, or
caption intelligence. TikTok publishing semantics, retry/backoff, reconciliation, scheduling,
and user ownership were not redesigned — `TikTokPublisher` itself was not modified at all, and
every existing test protecting those semantics passed unchanged.

## Required Final Report

**Milestone 3.4 — Object Storage + Media Lifecycle**

**Provider**
- Selected: Supabase Storage, via its REST API (plain `requests`, no new SDK dependency).
- Rationale: evaluated on its own merits (durable/private/user-scoped/cost/large-file/
  operational-simplicity criteria); already-provisioned project meant zero additional signup
  friction, but that was not the deciding factor by itself.

**Storage architecture**
- Protocol: `storage.protocol.StorageProtocol` (put/exists/delete/materialize).
- Local implementation: `storage.local.LocalStorage` — dev/test default, no network.
- Hosted implementation: `storage.supabase_storage.SupabaseStorage` — real Supabase Storage.
- Factory: `storage.factory.build_storage()` — `STORAGE_BACKEND`-driven, no silent fallback.

**Canonical media model**
- DB representation: `videos.storage_provider`/`storage_key`, both nullable, both backends.
- Object-key format: `users/<user_id>/videos/<video_id>/source<ext>` — deterministic,
  tenant-scoped.
- Ownership: verified at the application boundary before every storage operation
  (`media.media_storage`'s `MediaOwnershipError` check).

**Privacy/security**
- Bucket visibility: private (both `pickle-batch-media` and `pickle-batch-test`).
- Tenant isolation: proven — wrong-user upload/materialize attempts rejected before any storage
  call.
- Server credential handling: service-role key, server-side only, never logged/printed/exposed
  client-side.

**Materialization**
- Local temp strategy: `tempfile.NamedTemporaryFile`, cleaned up in `finally`, both backends.
- ffmpeg/transcription: unaffected — `media/inspection.py`/`transcription.py` unmodified, receive
  a plain local `Path` regardless of backend.
- Publisher: `scheduling/publish_tiktok.py` resolves storage before calling
  `TikTokPublisher.publish()`, which itself never learns about object storage.
- Cleanup: verified on both success and exception paths.

**Media processing**
- Old local-path assumptions removed: local ingestion's own assumptions were not removed
  (deliberately — see below), but the constraint that ingestion was the *only* processing path
  was — `process_one` gained optional `on_failed`/`on_assigned` hooks (default to the exact
  pre-3.4 `_move_file` behavior) and a new `process_storage_backed_video` entry point reuses that
  same logic against an object-storage materialization instead. Added on review — see Phase 10
  and ADR-0009's "Media processing" correction.
- Processing flow: local CLI ingestion (`process_one` via `content/incoming/`) is unchanged.
  Hosted processing (`process_storage_backed_video`) is new, additive, and does not require or
  create a permanent local copy — verified directly
  (`tests/test_process_storage_backed_video.py`).

**Lifecycle**
- Upload: `media.media_storage.upload_canonical_media` (idempotent, deterministic key) for an
  already-locally-processed video; a future upload API would instead insert a video row with
  `storage_provider`/`storage_key` set directly, with no local processing history — the shape
  `process_storage_backed_video`'s own tests simulate.
- Processing: local CLI ingestion unchanged; hosted processing
  (`process_storage_backed_video`) added, reusing `process_one`'s core logic.
- Publishing: `execute_claimed_platform_post`/`publish_video`/`worker.run_due_posts_once` all
  gained an optional `storage` parameter.
- Terminal-state rule: not yet evaluable (single-platform product) — deferred explicitly.
- Retention: keep indefinitely, documented decision (ADR-0009), not an oversight.
- Deletion: not implemented for canonical media on either backend this milestone.

**Postgres**
- Migration file: `persistence/postgres_migrations/0002_add_media_storage_columns.sql` (new,
  `0001` untouched).
- Schema changes: `videos.storage_provider`/`storage_key`, both nullable `TEXT`.
- Existing rows preserved: yes, verified.

**Existing media migration**
- Dry-run: matched the real run exactly (5 videos, would_migrate → migrated).
- Objects migrated: 5 (all real production videos, 56MB total).
- IDs preserved: yes (video IDs untouched — only new columns written).
- Hashes verified: yes, zero mismatches.
- Original local files retained: yes, all 5 confirmed present after migration.

**Failure handling**
- Missing object: `StorageObjectNotFoundError`, distinct from a transport/HTTP failure.
- Transient storage failure: `StorageError(reason_code="NETWORK_ERROR"/"HTTP_ERROR")`, mirrors
  `PublishError`'s structured shape — never conflated with a permanent media failure.
- Cleanup failure: temp-file cleanup uses `missing_ok=True`/`finally`, so a failed publish still
  cleans up.

**Testing**
- Focused: 41 (no network dependency — includes 7 for the review-added hosted processing entry
  point, `tests/test_process_storage_backed_video.py`).
- Hosted-storage integration: 9 (skipped automatically without credentials; verified with a real
  unset-credentials run).
- Full suite: 696 passed.
- Previous baseline: 646.

**End-to-end**
- Postgres: real (`PostgresContentStore`, `public`-schema-equivalent test schema).
- Object storage: real (`SupabaseStorage`, test bucket).
- Processing: real (`process_storage_backed_video` — real `ffprobe` inspection, real FIFO slot
  assignment — added on review so the end-to-end test starts from upload, not from an
  already-prepared video).
- FakePublisher: yes, no live TikTok call.
- Resulting status: `PUBLISHED`, temp materialization confirmed cleaned up (both the processing
  stage's and the publish stage's).

**External effects**
- TikTok calls: zero.
- Media deleted: none.
- Credentials exposed: none.

**Documentation**
- Architecture: `docs/architecture/hosted-product-boundary.md` (updated in place).
- Evaluation: this record.
- ADR: `docs/decisions/0009-object-storage-media-lifecycle.md`.

**Deferred**
- Upload UI: not built (guardrail).
- Hosted workers: not built.
- Real auth: not built; unaffected by this milestone.
- Multi-platform: not built; deletion/retention policy explicitly deferred until it exists.

**Overall**

COMPLETE — all 16 acceptance criteria satisfied: hosted object storage provider selected and
documented; runtime code depends on `StorageProtocol` rather than permanent local paths for both
the publishing materialization path and (added on review, closing a real architecture gap — see
Phase 10) the new hosted processing entry point, while local CLI ingestion is provably unchanged
and remains fully supported for local development; local storage retained for tests/dev;
canonical media has a durable logical storage reference; object keys are explicitly user-scoped;
raw media is private by default; ffmpeg/Whisper/TikTok all consume media through temporary
materialization with zero changes to those modules themselves; temporary files are cleaned up on
success and failure for both the processing and publishing paths (verified both ways, both
paths); tenant isolation prevents cross-user media access (verified, including that ownership is
checked *before* any storage call — proven with a spy storage implementation); Postgres schema
evolved through a new forward migration, not a rewrite of `0001`; existing local media has a
deterministic, hash-verified migration path that never deletes the source; existing publishing/
scheduling state is unchanged (696/696, one legitimate test-count-assertion update, zero other
tests modified); a real object-storage round trip succeeded; a real Postgres + real object
storage + FakePublisher end-to-end flow succeeded starting from upload and processing, not only
from an already-prepared video; no unintended TikTok API calls occurred. Do not commit until
reviewed.
