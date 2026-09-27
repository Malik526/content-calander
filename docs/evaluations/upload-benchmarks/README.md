# Upload Benchmarks

Raw comparison/testing data for real hosted-upload behavior — currently the
before/after check for the re-upload-architecture change
(`docs/decisions/0009-object-storage-media-lifecycle.md`'s follow-up addendum; see
`CHANGELOG.md`'s "Re-upload Architecture" entry). This folder is **evaluation
record-keeping, not product code or a runtime feature** — no script or background agent
exists here (see "Why no script/skill" below).

## Location and split from milestone docs

Originally this lived as a single file directly under `docs/evaluations/productization/`
(`milestone-3.7-upload-benchmark-snapshot.md`), alongside the Milestone 3.x status docs.
As iterations accumulated, that made the milestone doc area noisy: milestone docs are
meant to stay concise status/decision records, not grow raw per-iteration benchmark
tables. This folder is the dedicated home for that raw data instead:

- **`docs/evaluations/<domain>/milestone-X.Y-<name>.md`** — concise milestone
  status/decisions (unchanged convention, per `AGENTS.md`'s "Documentation / Evaluation
  Requirements"). `milestone-3.7-upload-benchmark-snapshot.md` now just points here.
- **`docs/evaluations/upload-benchmarks/`** (this folder) — one file per test iteration,
  plus a running comparison, kept simple and structured for side-by-side reading. Still
  inside the existing `docs/evaluations/` tree (not a new parallel top-level tree), just
  its own domain-like subfolder rather than nested under `productization/`.

## Files

- `iteration-1-original-upload.md` — the original three-video hosted-upload batch,
  pre-re-upload-architecture.
- `iteration-2-reupload.md` — after re-uploading, post-re-upload-architecture (not yet
  captured).
- `comparison.md` — the two iterations side by side, plus the optimization-justified
  verdict (not yet filled in).

## Why no script/skill

Per this evaluation's own explicit instruction, and the recommendation given alongside
it: this is a one-off (or few-off), temporary comparison for a single milestone, not a
recurring benchmark. A documented, copy-pasteable workflow (below) is enough; a script or
evaluation skill would be premature automation for something that hasn't been run even
once yet. If this benchmark ends up being repeated five or ten times, *that* would justify
turning it into a real script or skill — not before.

## What this captures, and what it never does

**Captured** (all non-secret, all already exposed by this codebase's own schema/telemetry
— see `persistence/content_store.py`'s `videos`/`upload_batches`/`upload_attempts`
tables): video id, filename, file size, file hash, storage provider, storage key (path
*shape*, not a credential — see `media/media_storage.py`'s `build_storage_key`),
created/processed timestamps, batch/attempt timing (`duration_ms`, `total_duration_ms`),
statuses and `error_code`s, which media-metadata columns are populated vs. `NULL`
(container/video_codec/audio_codec/width/height/fps/duration_seconds — expected all
`NULL`, see `media/media_storage.py`'s "Future media intelligence" section), Library UI
behavior, and any anomalies observed.

**Never captured, under any circumstance** — access tokens, refresh tokens, any secret,
encryption keys, OAuth codes/state values. The queries below only ever touch
`users.id/email/created_at` and `videos`/`upload_batches`/`upload_attempts` — never
`platform_credentials`, `oauth_states`, or `auth_identities.provider_subject`.

## How to capture a snapshot (the reusable template)

Run this whenever a snapshot is needed (a new iteration, or any future one-off
comparison) — via the Supabase SQL editor, or `psql "$DATABASE_URL"`, against the real
hosted database. This is a **read-only** workflow; nothing here writes anything.

1. **Resolve your user id** (never hardcode it — it can differ between environments):

   ```sql
   select id, email, created_at from users where email = 'your-account-email@example.com';
   ```

2. **Videos** (substitute the id from step 1):

   ```sql
   select id, original_filename, file_size_bytes, file_hash, storage_provider, storage_key,
          canonical_media_path, container, video_codec, audio_codec, width, height, fps,
          duration_seconds, status, created_at, processed_at
   from videos
   where user_id = <id>
   order by created_at desc;
   ```

3. **Upload batches:**

   ```sql
   select id, started_at, completed_at, file_count, total_bytes, total_duration_ms, status
   from upload_batches
   where user_id = <id>
   order by started_at desc;
   ```

4. **Upload attempts:**

   ```sql
   select id, batch_id, video_id, original_filename, file_size_bytes, started_at,
          completed_at, duration_ms, status, error_code
   from upload_attempts
   where user_id = <id>
   order by started_at desc;
   ```

5. **Derive throughput** (do not re-run the upload to "measure" this — derive it from
   what step 2/3 already captured, per this milestone's own "keep stored telemetry
   minimal, derive at query time" convention):

   `throughput_bytes_per_sec = file_size_bytes / (duration_ms / 1000)` per attempt;
   `batch_throughput = total_bytes / (total_duration_ms / 1000)` per batch.

6. **Check the real Supabase Storage bucket directly** (dashboard, or the Storage API) —
   confirm the objects actually exist at the `storage_key` paths from step 2, and note
   their reported size there too (a second, independent source for file size, not just
   the DB row's own `file_size_bytes`).

7. **Exercise the Library UI** — load `/app/library`, note what's shown (filenames,
   sizes, order, any errors), and whether navigating away mid-upload and back still
   shows the correct end state (Milestone 3.7 follow-up's navigation-safe upload
   provider).

8. **Record anomalies** — anything that doesn't match what the code/docs say should be
   true (e.g. a `storage_provider` that doesn't match the configured backend, a `NULL`
   `storage_key`, an unexpectedly large `duration_ms`).

Paste steps 2-4's raw output, plus 6-8's observations, into a new
`iteration-N-<name>.md` file in this folder, following `iteration-1-original-upload.md`'s
shape, then update `comparison.md`.
