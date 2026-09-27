# Iteration 2 — after re-upload, post-re-upload-architecture

**Status: preliminary attempt made 2026-09-27, real capture not started.** Capture the
real Iteration 2 snapshot after raising the Supabase Storage bucket's `file_size_limit`
and re-uploading the same two surviving files from Iteration 1 (video 8's original bytes
are gone — see `iteration-1-original-upload.md`; a two-file comparison is still a valid
before/after check for the re-upload architecture, just not a three-file one). Use the
*same* filenames/files as Iteration 1 so file size is directly comparable, and
deliberately navigate away from Library mid-upload and back at least once (per Milestone
3.7 follow-up's `UploadProvider`) before reloading the final state. Follow `README.md`'s
"How to capture a snapshot" template.

## Preliminary attempt (2026-09-27, before the size limit was raised)

A re-upload of three files (videos 9, 10, 11) was attempted before the Supabase Storage
size limit was raised. 2 of 3 failed with a real `413 EntityTooLarge` (videos 9 and 11);
only video 10 (49,564,468 bytes) succeeded. Full investigation, exact sizes, and the
upload-failure-semantics/telemetry-accuracy fixes this triggered are in `CHANGELOG.md`'s
2026-09-27 entries. This is **not** the real Iteration 2 capture — it predates both the
size-limit raise and the telemetry fixes below — but its data is real production history
and is recorded here rather than discarded:

**Known data-quality gap in this batch's telemetry (`upload_batches.id=1`):** at the time
of this attempt, a failed attempt's `file_size_bytes` was silently discarded instead of
recorded (fixed same day — see `CHANGELOG.md`), so the true sizes of videos 9 and 11 were
never captured anywhere. After migration `0006_upload_failure_semantics_and_byte_accounting.sql`
renames `total_bytes` to `attempted_bytes`, this specific historical row's
`attempted_bytes` will still read `49,564,468` — video 10's size alone — because that is
literally all `total_bytes` ever held under the old (buggy) semantics, which only ever
summed successful files. Under the new semantics `attempted_bytes` should be the sum of
all three files' sizes, but the other two were never measured and cannot be
backfilled retroactively. **Do not treat this one row's `attempted_bytes` as accurate
after the migration runs — it understates the true attempted bytes for this batch
specifically, and only this batch** (every batch recorded after the telemetry fix will be
correct going forward).

## Videos

| video_id | filename | file_size_bytes | file_hash (first 12 chars) | storage_provider | storage_key | status | created_at |
|---|---|---|---|---|---|---|---|
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |

## Upload batches

| batch_id | file_count | attempted_bytes | successful_bytes | success_count | failure_count | total_duration_ms | status |
|---|---|---|---|---|---|---|---|
| _pending — real capture_ | | | | | | | |

## Upload attempts

| attempt_id | batch_id | video_id | file_size_bytes | duration_ms | status | error_code |
|---|---|---|---|---|---|---|
| _pending_ | | | | | | |

## Duplicate/re-upload behavior

Confirm each of the two files got its own new `video_id` distinct from Iteration 1's
(not rejected, not silently reusing the old row — see `CHANGELOG.md`'s "Re-upload
Architecture" entry).

## Navigation-safe upload behavior

Confirm the batch that was in flight while navigating away still completed and was
reflected correctly on returning to Library.

## Library UI behavior observed

_pending_.

## Anomalies observed

_pending_.
