# Iteration 1 — original upload, before re-upload architecture changes

**Status: captured 2026-09-26, PARTIAL — 2 of the original 3 videos.** Read-only queries
run directly against the real hosted database (`users`, `videos`, `upload_batches`,
`upload_attempts`; never `platform_credentials`/`oauth_states`/`auth_identities`, per
this folder's own scope — see `README.md`). The third original video (id 8) had already
been deleted (via the Library's Delete Video action, while diagnosing an unrelated
CORS/migration production incident — see `CHANGELOG.md`'s 2026-09-26 entries) before
this snapshot was taken.

**Video 8's data is confirmed unrecoverable, not merely unchecked:**
`upload_batches`/`upload_attempts` exist as of this capture (migrations
`0004_add_upload_telemetry.sql`/`0005_drop_videos_file_hash_uniqueness.sql` were applied
today) but **both tables are completely empty — zero rows, for any of the three original
videos, not just video 8.** Reason, confirmed by comparing timestamps: all three original
videos were created at `2026-09-26 05:36:01 UTC`, which is *before*
`api/routes/videos.py` was ever deployed with the `store.create_upload_batch()`/
`create_upload_attempt()` calls that populate those tables (that instrumentation shipped
in commit `c6b3c0d`, deployed ~`2026-09-26 06:41 UTC` — roughly an hour later). So no
telemetry row was ever created for this original batch, for any of its three files,
regardless of table existence — there was never anything to preserve. Video 8's
`storage_provider` was almost certainly `local` (matching its two batch-mates below,
uploaded in the same request under the same `STORAGE_BACKEND` misconfiguration — see
`CHANGELOG.md`'s Milestone 3.7 follow-up entry), meaning its bytes lived on Railway's own
ephemeral local filesystem rather than Supabase Storage, with no independent
object-storage trace to check either, especially after at least one redeploy since (the
CORS fix, commit `6949a28`). No filename/size/hash for video 8 was ever recorded outside
its own now-deleted `videos` row. This is a genuine, permanent gap in Iteration 1, not a
recoverable one.

## Videos

| video_id | filename | file_size_bytes | file_hash (first 12 chars) | storage_provider | storage_key | status | created_at |
|---|---|---|---|---|---|---|---|
| 6 | `cd4e03e2f5ba465f84690160d8f93ae5.mov` | 61,914,933 | `472d04a6a3d1` | local | `users/2/videos/6/source.mov` | DISCOVERED | 2026-09-26T05:36:01.506089+00:00 |
| 7 | `copy_D0BD96B9-3E00-4F08-9BC5-541981EF2F7B.mov` | 49,564,468 | `bcda11655d15` | local | `users/2/videos/7/source.mov` | DISCOVERED | 2026-09-26T05:36:01.702566+00:00 |
| 8 | _unrecoverable — deleted before this snapshot, no telemetry row ever existed (see above)_ | | | | | | |

## Upload batches

| batch_id | file_count | total_bytes | total_duration_ms | status |
|---|---|---|---|---|
| _none — table exists but has 0 rows; this upload predates the telemetry instrumentation (see above), not a data-loss finding_ | | | | |

## Upload attempts

| attempt_id | batch_id | video_id | file_size_bytes | duration_ms | status | error_code |
|---|---|---|---|---|---|---|
| _none — same reason as upload batches above_ | | | | | | |

## Media-metadata fields

`container`/`video_codec`/`audio_codec`/`width`/`height`/`fps`/`duration_seconds`:
confirmed all `NULL` for both video 6 and video 7 — matches expectation (no ffprobe
enrichment exists yet for hosted uploads).

## Storage bucket check (playbook step 6)

Not applicable — both surviving videos (and, by inference, video 8) have
`storage_provider='local'`, not `supabase`, so there is no Supabase Storage object to
look up for this batch at all; this is the same `STORAGE_BACKEND` env-var gap already
documented in `CHANGELOG.md`'s Milestone 3.7 follow-up entry, not a new finding.

## Library UI behavior observed

Not captured in this pass — out of scope for this snapshot's requested field list
(video/batch/attempt schema data for a before/after comparison), and not needed to
establish the baseline. Skip noted here rather than fabricated.

## Anomalies observed

1. Both videos' `storage_provider` is `local` despite `STORAGE_BACKEND=supabase` having
   been set in Railway's dashboard by the time of capture — already-documented,
   pre-existing issue, not something this snapshot newly discovered (the fix would
   require re-uploading after confirming the env var was live *at upload time*, which it
   evidently wasn't for this batch).
2. Iteration 1 is permanently limited to 2 of the original 3 videos for the reasons above.
