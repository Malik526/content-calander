# Milestone 3.7 — Upload Benchmark Evaluation Snapshot

A temporary, repeatable evaluation workflow for comparing real hosted-upload behavior
before and after the re-upload-architecture change (`docs/decisions/0009-object-storage-media-lifecycle.md`'s
follow-up addendum; see `CHANGELOG.md`'s "Re-upload Architecture" entry). This is
**evaluation record-keeping, not product code or a runtime feature** — no script or
background agent was added to the repository for this (see "Why no script/skill" below).

## Location

This lives in `docs/evaluations/productization/` — the same domain folder as every other
Milestone 3.x validation-evidence doc (`milestone-3.7-batch-upload-readiness.md` and its
own addenda are right next to this file) — rather than either of the two other candidate
locations in this repo:

- **`evaluation/video_pipeline/`** — the wrong domain and the wrong visibility. That
  directory is a private, **gitignored** golden dataset for classifier/transcription
  benchmarking against real creator content (see `evaluation/README.md`); a committed,
  comparable Iteration 1 vs. Iteration 2 snapshot needs to actually be tracked in git,
  and has nothing to do with classification.
- **A brand-new top-level `evaluations/video-pipeline/upload-benchmark/`** (the shape
  the originating brief suggested as an example) — this repo already has an established,
  working convention for exactly this kind of artifact
  (`docs/evaluations/<domain>/milestone-X.Y-<name>.md`, per `AGENTS.md`'s own
  "Documentation / Evaluation Requirements"), so a new parallel top-level tree would only
  fragment where evidence lives. Following the existing pattern instead — this file's own
  name — needed no new directory.

## Why no script/skill

Per this milestone's own explicit instruction, and the recommendation given alongside
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

Run this whenever a snapshot is needed (Iteration 1, Iteration 2, or any future one-off
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

Paste steps 2-4's raw output, plus 6-8's observations, into the matching Iteration
section below.

## Iteration 1 — baseline before re-upload architecture changes

**Status: not yet captured.** This agent session cannot read the real hosted Postgres
database directly — the sandbox's own auto-mode classifier denies direct reads against
production data (`Reason: [Production Reads]`), confirmed again in this session before
writing this doc, and (per this repository's own established policy — see
`~/.agents/SECURITY.md` and this milestone's own prior evaluation docs) that restriction
is not something this agent attempts to work around.

**Action needed from you, before deleting the three existing test videos:** run the
queries in "How to capture a snapshot" above against the real database, and paste the
output back (or fill the tables below yourself). Once that's done, this section should
be updated to replace this placeholder with the real captured values, and its heading
changed to record the actual capture date.

| video_id | filename | file_size_bytes | file_hash (first 12 chars) | storage_provider | storage_key | status | created_at |
|---|---|---|---|---|---|---|---|
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |

| batch_id | file_count | total_bytes | total_duration_ms | status |
|---|---|---|---|---|
| _pending_ | | | | |

| attempt_id | batch_id | video_id | file_size_bytes | duration_ms | status | error_code |
|---|---|---|---|---|---|---|
| _pending_ | | | | | | |

**Media-metadata fields (container/video_codec/audio_codec/width/height/fps/duration_seconds):**
expected all `NULL` for every row — confirm this is actually true, not merely expected.

**Library UI behavior observed:** _pending_.

**Anomalies observed:** _pending_.

## Iteration 2 — after re-upload + navigation-safe validation

**Status: not started** — capture this after re-uploading the same three files
following the workflow above. Use the *same* filenames/files as Iteration 1 so file
size is directly comparable, and deliberately navigate away from Library mid-upload and
back at least once (per Milestone 3.7 follow-up's `UploadProvider`) before reloading the
final state.

| video_id | filename | file_size_bytes | file_hash (first 12 chars) | storage_provider | storage_key | status | created_at |
|---|---|---|---|---|---|---|---|
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |

| batch_id | file_count | total_bytes | total_duration_ms | status |
|---|---|---|---|---|
| _pending_ | | | | |

| attempt_id | batch_id | video_id | file_size_bytes | duration_ms | status | error_code |
|---|---|---|---|---|---|---|
| _pending_ | | | | | | |

**Duplicate/re-upload behavior:** confirm each of the three files got its own new
`video_id` distinct from Iteration 1's (not rejected, not silently reusing the old row —
see `CHANGELOG.md`'s "Re-upload Architecture" entry).

**Navigation-safe upload behavior:** confirm the batch that was in flight while
navigating away still completed and was reflected correctly on returning to Library.

**Library UI behavior observed:** _pending_.

**Anomalies observed:** _pending_.

## Comparison

| Metric | Iteration 1 | Iteration 2 | Delta | Observation |
|---|---|---|---|---|
| File size vs. duration (per file) | _pending_ | _pending_ | _pending_ | |
| Per-file upload duration (`duration_ms`) | _pending_ | _pending_ | _pending_ | |
| Total batch duration (`total_duration_ms`) | _pending_ | _pending_ | _pending_ | |
| Derived throughput (bytes/sec) | _pending_ | _pending_ | _pending_ | |
| Storage behavior (provider/key correctness) | _pending_ | _pending_ | _pending_ | |
| DB schema correctness (fields null/populated as expected) | _pending_ | _pending_ | _pending_ | |
| Duplicate/re-upload behavior | N/A (no re-upload attempted yet) | _pending_ | _pending_ | |
| Navigation-safe upload behavior | _pending_ | _pending_ | _pending_ | |
| Library results | _pending_ | _pending_ | _pending_ | |

**Whether optimization is justified:** _pending_ — fill in once both iterations are
captured. As a baseline expectation going in: with only three small files and no
architectural change to the upload path itself in this pass (Part A only changed
`file_hash`'s uniqueness semantics, not the upload/storage/DB write sequence), Iteration
2's timings should land close to Iteration 1's — a large, unexplained delta in either
direction would itself be the interesting finding, not the absolute numbers.
