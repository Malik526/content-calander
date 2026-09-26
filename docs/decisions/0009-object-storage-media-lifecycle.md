# ADR-0009: Object Storage and Media Lifecycle

## Status

Accepted

## Context

Through Milestone 3.3, every canonical video file lives permanently on the local filesystem
(`content/processed/`), referenced by `videos.canonical_media_path` as a real machine-specific
absolute path — the last remaining piece of hosted-product state still tied to one computer,
after Milestone 3.3 moved the business database itself to Postgres. Milestone 3.4 removes that
dependency **for the hosted publishing and hosted processing paths specifically**: a durable,
tenant-scoped object-storage reference becomes the canonical identity for a video that has
actually been uploaded to object storage, and local files on that path are only ever temporary
materializations, produced on demand for tools that genuinely require a real path (`ffprobe`,
`ffmpeg`, `faster-whisper`, TikTok's `FILE_UPLOAD`).

**This is not a claim that local files stopped being durable everywhere.** Local CLI ingestion
(`cli/process_content.py` → `media.processing.process_one` → `content/incoming/` →
`content/processed/`|`content/failed/`) is unmodified and remains a fully supported, permanent-
local-file workflow for local development — see "Media Processing" below for exactly what did
and did not change there, corrected during review after an earlier draft of this document
overstated the scope as "removes that dependency" without qualification.

This must happen without redesigning TikTok publishing semantics, retry/backoff, reconciliation,
scheduling, or user ownership, without building object storage's full deletion/retention
machinery prematurely, and without forcing every existing test (696 by the end of this
milestone) to depend on network storage.

## Decision

### Provider: Supabase Storage, evaluated on its own merits

The same Supabase project already hosts Postgres (Milestone 3.3), but object storage is a
genuinely separate decision, evaluated against its own requirements (durable, private,
user-scoped naming, Python-backend-compatible, reasonable early-stage cost, large-video support,
operational simplicity) — not chosen merely for stack consistency. It satisfies them, and having
one already-provisioned project meant zero additional signup friction. **Choosing Supabase
Storage did not mean choosing Supabase Auth** — nothing about authentication was touched.

Implemented against Supabase Storage's plain REST API via `requests` (already a dependency this
codebase uses throughout `publishing/tiktok/*`), not the `supabase-py`/`storage3` SDK. The
exact API shapes (`POST .../object/{bucket}/{key}` with `x-upsert: true` to upload, `HEAD` to
check existence, `GET` streamed to download, `DELETE .../object/{bucket}` with a
`{"prefixes": [...]}` body to remove) were verified directly against a real Supabase project
before any production code was written, not assumed from documentation — see the Milestone 3.4
evaluation record's "Storage Provider" phase.

Two private buckets were created via the Storage API (not the dashboard): `pickle-batch-media`
(the real application bucket) and `pickle-batch-test` (disposable, integration-test-only —
mirrors `POSTGRES_TEST_SCHEMA`'s role from Milestone 3.3 exactly). Both are private by default —
no bucket was made public for convenience; every real access goes through the server-side
service-role key, never a browser-exposed credential.

### Storage Contract: a small four-method Protocol, not a general filesystem abstraction

`storage.protocol.StorageProtocol` (`typing.Protocol`, the same structural-typing pattern
`persistence.protocol.ContentStoreProtocol` established in Milestone 3.3 — no inheritance
imposed on either implementation): `put`, `exists`, `delete`, and `materialize` (a context
manager yielding a real local `Path` for the duration of a `with` block, cleaned up on exit
whether the block succeeds or raises). Deliberately not a larger abstraction — this is "how do I
store bytes, get a local path for tools that require one, and remove an object," not a
general-purpose filesystem layer.

### Two parallel concrete implementations, mirroring Milestone 3.3's persistence decision exactly

`storage.local.LocalStorage` (filesystem-backed, the dev/test default — mirrors `ContentStore`'s
role) and `storage.supabase_storage.SupabaseStorage` (the real hosted backend — mirrors
`PostgresContentStore`'s role), selected by `storage.factory.build_storage()` based on
`config.STORAGE_BACKEND` (`"local"` default, `"supabase"` to enable the hosted backend) — the
same "configured hosted backend that's unreachable raises, never silently falls back" philosophy
`persistence.store_factory.build_content_store()` established for `DATABASE_URL`. Each carries a
`provider_name` attribute (`"local"`/`"supabase"`) so `media.media_storage.upload_canonical_media`
always stamps the DB row with the backend that *actually* holds the bytes, read from the
instance in use — never hardcoded by a caller.

### Canonical media reference: new columns, existing columns untouched in meaning for legacy rows

Two new nullable columns on `videos` — `storage_provider`, `storage_key` — added via SQLite's
existing additive-column pattern (`_ensure_videos_columns`) and a new Postgres forward migration
(`0002_add_media_storage_columns.sql`, **not** a rewrite of `0001_initial_schema.sql`, which is
already applied against real production data). `original_path`/`canonical_media_path` are
untouched in both schema and meaning: for a video with `storage_provider IS NULL` (every video
that existed before this milestone, and any new one not yet uploaded), `canonical_media_path` is
still the authoritative local filesystem path — exactly pre-3.4 behavior, verified by the full
pre-existing test suite passing completely unchanged. Once `storage_provider`/`storage_key` are
both set, they become authoritative instead; `canonical_media_path` is left as historical lineage
(what the file was, and last known to be, on disk), never cleared, never treated as canonical
again.

Both new columns are nullable at the SQL level under Postgres too, unlike Milestone 3.3's
`user_id` decision — deliberately different: `user_id` was made `NOT NULL` because every real row
was already ownership-complete at the moment that migration ran (Milestone 3.2 had already
backfilled it). No real video row has ever had a `storage_key` before this migration exists, so
there is nothing to backfill before a `NOT NULL` constraint could apply, and every existing real
video must keep working exactly as today until explicitly migrated by
`cli/migrate_media_to_object_storage.py`.

### Object key format: deterministic and tenant-scoped

`users/<user_id>/videos/<video_id>/source<ext>` (`media.media_storage.build_storage_key`) — not
a random UUID. Deterministic for two reasons: it makes a repeated upload for the same video
naturally idempotent at the storage layer (`put`'s upsert contract simply overwrites the same
key, never creates a duplicate — see "Concurrency/Duplicate Processing" below), and it keeps
every object inspectable/attributable by path alone without a database lookup. No user-provided
filename or secret information is embedded.

### Tenant isolation at the application boundary, not just bucket permissions

`media.media_storage.upload_canonical_media`/`materialize_canonical_media` both resolve the
video's owner and verify it matches the `user_id` the caller supplied *before* touching storage
at all — raising `MediaOwnershipError` otherwise. This is enforced in application code, not left
to rely on the obscurity of an object key or bucket-level permissions alone (per the brief's own
caution) — verified directly:
`tests/test_media_storage.py::test_materialize_canonical_media_rejects_wrong_user` and the
matching upload-side test prove a known video_id cannot be resolved by the wrong user, even
though the underlying storage key is fully deterministic and therefore guessable in principle.

### Materialization boundary: the job/application layer resolves storage, `TikTokPublisher` never learns about Supabase

`scheduling/publish_tiktok.py`'s `execute_claimed_platform_post`/`publish_video` gained an
optional `storage` parameter. For a video with no `storage_provider` (every pre-3.4 video), it is
never even consulted — the exact pre-3.4 code path, verified unchanged by the full existing test
suite. For a storage-backed video, the row is materialized to a real temp local path
(`media.media_storage.materialize_canonical_media`) for the duration of validation and
submission, then cleaned up; `TikTokPublisher.publish()` itself still only ever receives a plain
`Path` — it has no idea whether that path came from `content/processed/` or a temp file streamed
down from Supabase moments earlier. `worker.run_due_posts_once` gained the same optional
`storage` parameter, forwarded unchanged.

### Media processing: a first-class hosted entry point, added during review

**Correction note:** an earlier draft of this milestone left `media/processing.py` completely
untouched and stated local ingestion was "not modified" without qualifying what that meant for
the milestone's own stated objective. That was accurate about the *code* but misleading about
the *architecture claim* — `media.processing.process_one`/`discover_videos`/`_move_file`
remained the *only* processing path, meaning a storage-backed video (one uploaded but never
locally processed) had no way to be inspected/transcribed/captioned/scheduled without first
being forced through a permanent `content/incoming/`→`content/processed/` round trip, which
directly contradicted this document's own "local files are only ever temporary
materializations" framing. Found and fixed during review, before this milestone was treated as
closed.

**Fix:** `process_one` gained two optional hooks, `on_failed`/`on_assigned` (`Callable[[Path],
None]`/`Callable[[Path], Path | None]`), defaulting to `_move_file(path, FAILED_DIR)`/
`_move_file(path, PROCESSED_DIR)` — the exact pre-3.4 behavior, unchanged for every existing
caller (`cli/process_content.py`, every pre-existing test) that doesn't pass them. A new
`media.processing.process_storage_backed_video(store, storage, transcriber, classifier,
video_id, user_id, ...)` reuses `process_one`'s inspection/transcription/caption/scheduling
logic verbatim — not a duplicate pipeline — by materializing the video from object storage
(`media.media_storage.materialize_canonical_media`) into a real temp path, calling `process_one`
against that temp path with no-op hooks (nothing to move; the temp materialization's own context
manager cleans it up on the way out, success or exception), and relying on the same
content-hash identity (`file_hash`) `process_one` already uses for idempotency to resolve the
already-uploaded `videos` row rather than creating a duplicate. `TikTokPublisher`,
`media/inspection.py`, and `media/transcription.py` remain genuinely untouched — this
correction only added the hooks to `process_one` itself and the new entry point alongside it.

**What "local files are only ever temporary materializations" now accurately means:** true for
any video that has been uploaded to object storage, on both the publishing path (§"Materialization
boundary" above) and the processing path (this section). **Not true, and never intended to be
true, for local CLI ingestion** — `cli/process_content.py`'s `content/incoming/`→
`content/processed/`|`content/failed/` workflow remains permanent, local-first, and fully
supported for local development; nothing in this milestone deprecates or discourages it. A video
processed via local ingestion has no `storage_provider` set and is never touched by any of this
milestone's object-storage code paths unless separately migrated
(`cli/migrate_media_to_object_storage.py`).

### Retention: keep local sources indefinitely — a deliberate V1 choice, not an oversight

No deletion of canonical media (local or object-storage) was implemented this milestone, for
either backend. `StorageProtocol.delete()` exists (needed for the disposable-test-object
lifecycle, and for interface completeness/parity between backends) and is tested directly, but
no production code path ever calls it against real canonical media. Per the brief's own explicit
allowance ("V1 may choose: keep source indefinitely... document it explicitly rather than
implementing premature deletion") — multi-platform publishing (Instagram, YouTube) does not exist
yet, so there is no terminal-state rule to evaluate deletion against safely, and building that
logic now would be exactly the speculative design this project's own coding policy warns against.
The migration tool (`cli/migrate_media_to_object_storage.py`) never deletes the local original
either, for the same reason plus an additional one: it is the one thing standing between "real
business media" and "gone" until the upload has actually been verified.

### Concurrency / duplicate processing

No distributed lock was introduced. The deterministic storage key (above) already makes a
repeated `upload_canonical_media` call for the same video safe — the same object is overwritten,
not duplicated — and `ContentStore`'s existing per-video-row semantics (one `videos` row per
`file_hash`, one `platform_posts` row per `(video_id, platform)`) already prevent two workers from
processing the same video's publishing lifecycle twice, exactly as they did before this
milestone. No new locking subsystem was justified by anything this milestone's investigation
found.

### Large files: streamed, not buffered

Uploads stream from an open file handle (`requests.post(..., data=open(path, "rb"))`, never
`.read()`'d into memory first); downloads stream via `iter_content` directly to a temp file.
Verified with a real 5MB round trip against Supabase
(`tests/test_storage_supabase.py::test_large_ish_file_streams_without_loading_whole_object_into_memory`)
— not a claim tested only against tiny fixtures.

## Consequences

- Any future platform adapter (Instagram, YouTube) that needs local bytes should resolve them
  the same way `execute_claimed_platform_post` now does — `materialize_canonical_media`, never a
  direct read of `canonical_media_path` — so it inherits storage-backend transparency for free.
- Deletion/retention policy remains genuinely undecided — the milestone that adds a second
  platform (making "all intended platforms terminal" a real, evaluable condition) is the natural
  place to revisit this, not before.
- `cli/migrate_media_to_object_storage.py` and `cli/backfill_ownership.py`/
  `cli/migrate_sqlite_to_postgres.py` (Milestones 3.2/3.3) now form a consistent family of
  one-off, idempotent, verify-before-trusting migration scripts — any future one should follow
  the same shape (dry-run support, hash/field verification, never delete the source until
  explicitly asked).
- `STORAGE_BACKEND` defaulting to `"local"` means a fresh clone of this repository needs zero
  object-storage configuration to run its full test suite or its existing local pipeline — the
  same "SQLite works with zero setup" property `DATABASE_URL` preserved in Milestone 3.3.
- A future upload API (Milestone 3.5+) has a real, tested target to call once a video's bytes
  exist in object storage: `media.processing.process_storage_backed_video`, not a new pipeline —
  the same "reuse, don't duplicate" pattern this correction itself demonstrated should continue.
- When documenting scope in future ADRs/evaluations, a claim like "X is only ever Y" should be
  read back against every real entry point before it ships, not just the one milestone's own new
  code — this correction exists because that check was skipped for the local-ingestion path the
  first time this document was written.

## Addendum (2026-09-26, Milestone 3.7 follow-up) — `storage_provider="local"` on real hosted uploads; `canonical_media_path` for a video with no local file at all

Real hosted browser uploads (`api/routes/videos.py`, `media.media_storage.create_video_from_upload` —
Milestone 3.7) were observed persisting `storage_provider="local"` in production. Investigated and
confirmed **not a code defect**: `create_video_from_upload`, exactly like `upload_canonical_media`
above, always stamps `storage_provider` from the *actual* injected `StorageProtocol` instance's
`provider_name` — never a literal string (grepped every write site). The real cause is this ADR's
own "Consequences" bullet a few lines up: `STORAGE_BACKEND` defaults to `"local"` whenever it is
unset, and — per Milestone 3.4's own scope note — `storage.factory.build_storage()` had genuinely
never been called from any live entry point until this milestone's upload endpoint. If the real
Railway deployment's environment variables were never explicitly given `STORAGE_BACKEND=supabase`
(plausible: nothing before this milestone would have surfaced the gap), `LocalStorage` is what
`get_storage()` really resolves to there, and the uploaded bytes are genuinely sitting under that
process's `LOCAL_STORAGE_ROOT` — not in the real Supabase bucket. A `storage_key` shaped like
`users/<user_id>/videos/<video_id>/source.mov` does **not** by itself prove which backend holds the
bytes — `build_storage_key()` is deliberately backend-agnostic and produces the identical string
regardless — only the `storage_provider` column (or a real check against the Supabase dashboard)
does. `api/app.py`'s existing startup diagnostic now also logs `STORAGE_BACKEND` for exactly this
reason — visible in Railway's own logs on every deploy from now on, not discovered later by
inspecting rows. No `videos.storage_provider` values are rewritten retroactively by this addendum —
correcting the Railway environment variable (an operational action, not a schema/code change) is
what makes new uploads record accurately going forward; see
`docs/evaluations/productization/milestone-3.7-batch-upload-readiness.md`'s follow-up entry.

Separately: `create_video_from_upload` leaves `canonical_media_path` `NULL` for every hosted
upload, deliberately, and this addendum confirms that was the right call rather than an oversight
worth revisiting. This ADR's "historical lineage" framing above (`canonical_media_path` = "what the
file was, and last known to be, on disk") implicitly assumed every video started life via local
ingestion and was *later* migrated to storage — a case this ADR did anticipate. A hosted browser
upload never has a local file at all beyond a request-scoped temp file that is deleted before the
response is even sent — there is no "history" to record, so `NULL` is the honest value, not a
gap. `storage_provider`/`storage_key` are this row's only reference from creation, exactly the
"both set, authoritative" case this ADR already describes — hosted uploads simply skip the
"local-only" and "both, with a stale-but-harmless local lineage" states entirely and go straight to
the third. Restated explicitly since it wasn't spelled out until asked: **there is exactly one
canonical hosted-media identifier — `storage_key` (paired with `storage_provider`) — and
duplicating that concept into `canonical_media_path` for a hosted upload would only invite the two
to drift.**

## Addendum (2026-09-26, Milestone 3.7 re-upload-architecture follow-up) — `videos.file_hash` is no longer globally unique

`videos.file_hash` was globally `UNIQUE` from this table's very first schema (predates this ADR).
That constraint conflated two different things: `videos.id` is a record's real identity;
`file_hash` is a content fingerprint. For local CLI ingestion (`media/processing.py`) that
distinction never mattered in practice — the pipeline only ever looks a hash up to *resume*
processing a physically-rediscovered file (crash recovery / restart-safety), so at-most-one-row-
per-hash was always true there by construction, uniqueness or not. For the hosted upload path
(`media.media_storage.create_video_from_upload`, Milestone 3.7) it did matter: the constraint meant
a creator intentionally re-uploading the exact same finished video later — a new caption, a new
schedule, a new campaign, a fresh performance history — either got silently handed back their
*original* row (same-user case) or was rejected outright with `DuplicateVideoContentError`
(different-user case), neither of which is correct: both are legitimate new records, not
duplicates to collapse or reject.

Dropped the constraint (`persistence/content_store.py`'s `_migrate_videos_drop_file_hash_uniqueness`
for SQLite — a structural rebuild, the same shape as this codebase's other "SQLite can't ALTER a
constraint in place" migrations; `postgres_migrations/0005_drop_videos_file_hash_uniqueness.sql`
for Postgres), replacing it with a plain index (hash-based lookups/future duplicate-detection
queries stay fast; nothing about `get_video_by_hash` needing to be fast changes). Removed
`DuplicateVideoContentError` entirely along with it — `create_video_from_upload` no longer looks
`file_hash` up before inserting at all; every upload call always creates a new row. This is a
genuine, deliberate behavior change from Milestone 3.7's original "idempotent per (user, exact
content)" design (see this same function's now-superseded prior docstring in git history) — not a
bug fix to that design, a reversal of it, made on this milestone's own explicit instruction.

**What did not change:** `videos.id` was always the primary key and the value every foreign key
(`content_slots.assigned_video_id`, `platform_posts.video_id`, `upload_attempts.video_id`) already
pointed at — nothing about ownership, tenant isolation, or any other table's referential integrity
depends on `file_hash` being unique, so removing that constraint has no bearing on any of them.
`get_video_by_hash` itself is unchanged (still `SELECT ... WHERE file_hash = ?`, returns *some*
match) — every caller that still assumes at-most-one-row-per-hash (`media/processing.py`'s local
pipeline) continues to satisfy that assumption itself, by construction, exactly as before; nothing
about this change makes that assumption newly unsafe for that caller. `insert_video` (SQLite) was
corrected alongside this to look its own just-inserted row up by `id`, never by `file_hash` — with
duplicate hashes now possible, re-resolving by hash after an insert could return a *different*
pre-existing row sharing that hash instead of the one just created.

**A narrower, pre-existing edge case this does not newly create, but does slightly widen:** local
CLI ingestion and hosted upload share the same `videos` table and the same hash function. If two
different users' hosted uploads now share a hash (newly possible — previously blocked at the
schema level), and a local CLI run later processes a physically-identical file, `get_video_by_hash`
will return *some* matching row, not necessarily the CLI-invoking user's own — `media/processing.py`
already does not check row ownership before mutating it (a documented, accepted limitation of the
single-operator local pipeline, unrelated to this change). This was already a latent risk under
per-user duplicate hosted uploads before this change (which the old constraint also didn't prevent
across the local/hosted boundary in the same way); it is unchanged in kind, only in that cross-
tenant hash collisions can now persist rather than being rejected at insert time. No fix is made
here — the local CLI pipeline remains an explicitly single-operator tool, and wiring hosted uploads
into that pipeline at all is still out of scope (see `AGENTS.md`'s "Scope Guardrails" and this ADR's
own "Media Processing" section above).
