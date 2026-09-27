# Iteration 2 — after re-upload, post-re-upload-architecture

**Status: not started** — capture this after re-uploading the same two surviving files
from Iteration 1 (video 8's original bytes are gone — see
`iteration-1-original-upload.md`; a two-file comparison is still a valid before/after
check for the re-upload architecture, just not a three-file one). Use the *same*
filenames/files as Iteration 1 so file size is directly comparable, and deliberately
navigate away from Library mid-upload and back at least once (per Milestone 3.7
follow-up's `UploadProvider`) before reloading the final state. Follow `README.md`'s
"How to capture a snapshot" template.

## Videos

| video_id | filename | file_size_bytes | file_hash (first 12 chars) | storage_provider | storage_key | status | created_at |
|---|---|---|---|---|---|---|---|
| _pending_ | | | | | | | |
| _pending_ | | | | | | | |

## Upload batches

| batch_id | file_count | total_bytes | total_duration_ms | status |
|---|---|---|---|---|
| _pending_ | | | | |

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
