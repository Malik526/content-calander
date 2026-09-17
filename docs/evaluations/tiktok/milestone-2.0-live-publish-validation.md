# Milestone 2.0 — Live TikTok Publish Validation

Validation evidence, not an architecture decision — see `docs/decisions/0006-tiktok-publisher-foundation.md` for the design this validates against. Recorded 2026-09-17.

## Purpose

Prove the real, live TikTok Direct Post publishing path end-to-end — not a mock, not a dry run — for exactly one manually-chosen already-processed video, using the existing production code path (`publish_tiktok.py` → `TikTokPublisher` → `platform_posts`), with no temporary or alternate script. This is the milestone's acceptance test: Milestone 2.0 is not COMPLETE until this path is demonstrated live, with persistence and idempotent-rerun behavior confirmed against the real database, not just unit-tested against mocks.

## Environment / Preconditions

- TikTok Sandbox app configured; `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET` set in local `.env`.
- Desktop PKCE authorization flow completed successfully (`python3 tiktok_auth.py --authorize`), following the same-day PKCE challenge-encoding fix (hex digest of SHA256, not base64url — TikTok-specific deviation from RFC 7636).
- Access/refresh token cached at `~/.config/content-calendar/tiktok_token.json`, confirmed present with the expected fields before this run (token values not inspected/printed).
- No code changes were made before or during this run.

## Test Subject

Selected by querying `videos` for the row closest to 25s duration, not assumed from memory:

| Field | Value |
|---|---|
| `videos.id` | 2 |
| File | `7020469266554080517.mp4` |
| Canonical path | `content/processed/7020469266554080517.mp4` |
| Duration | 26.05s (DB value cross-checked directly against `ffprobe` on the file — exact match) |
| Container / video codec | `mov` / `h264` (both within `TIKTOK_CONTAINERS`/`TIKTOK_VIDEO_CODECS`) |
| Caption length | 357 UTF-16 code units (limit: 2200) |
| Prior `platform_posts` row for `tiktok` | none — clean slate for this test |

"Video ideas" (or a similarly-named concept) was checked for and confirmed **not present anywhere in this codebase** — out of scope, not introduced into this test.

## Execution Path

Exercised the real, existing architecture exactly as designed — no one-off script:

1. `TikTokPublisher().query_creator_info()` — precondition check, run standalone before the publish attempt.
2. `python3 publish_tiktok.py --video-id 2` — first submission.
3. `python3 publish_tiktok.py --video-id 2 --poll-only` — status re-check after TikTok finished processing (no resubmission).
4. `python3 publish_tiktok.py --video-id 2` (no flags, rerun) — idempotency check against the now-`PUBLISHED` record.

## Results

**`creator_info`** (account `malikstewart70`):

- `privacy_level_options` included `SELF_ONLY` — required, since this is an unaudited client restricted to `SELF_ONLY` regardless of what else an account reports.
- `max_video_post_duration_sec`: 3600 — the 26.05s video is comfortably within limit.

**First publish attempt:**

- Submission accepted. `platform_post_id`: `v_pub_file~v2-1.7686313615539865614`.
- Immediate status: `PROCESSING_UPLOAD` (non-terminal, as expected for TikTok's async processing).
- After a short wait, `--poll-only` reported `PUBLISH_COMPLETE` — the post reached a real terminal success state on TikTok's side.

## Persistence Verification

`platform_posts` row for `video_id=2`, `platform='tiktok'`, inspected directly via SQLite at each stage:

| Stage | `status` | `platform_post_id` | `published_at` |
|---|---|---|---|
| Immediately after submission | `PUBLISHING` | set | empty |
| After `--poll-only` reported `PUBLISH_COMPLETE` | `PUBLISHED` | unchanged | set |

Confirms the designed crash-safety property: `platform_post_id` is persisted immediately on submission, separately from and before the eventual terminal-status write — a crash between submission and polling would leave a record the next run can safely re-poll rather than resubmit.

## Idempotency Verification

Reran `python3 publish_tiktok.py --video-id 2` (the plain form, not `--poll-only`) against the already-`PUBLISHED` record. Result:

- Output: `Video 2 is already PUBLISHED as TikTok post v_pub_file~v2-1.7686313615539865614.` — no new submission attempted.
- `platform_posts` row re-inspected: exactly one row for `video_id=2` (`COUNT(*) = 1`), `updated_at` byte-for-byte unchanged from before the rerun — zero side effects, not just "no duplicate," but no write of any kind occurred.

## Failures / Deviations

None. No precondition failed, no code change was required, and no deviation from the designed execution path (`videos` → `platform_posts` → `creator_info` → `SELF_ONLY` validation → duration validation → caption validation → init with `FILE_UPLOAD` → byte upload → `platform_post_id` → persist immediately → poll → final `platform_posts` state) was needed.

## Conclusion

The real, live, private TikTok Direct Post publishing path is proven end-to-end: submission, async processing, status polling, correct two-stage persistence, and idempotent-rerun protection against duplicate submission all behaved exactly as designed on the first live attempt.

**Relevant test suite** (`test_publish_tiktok.py`, `test_tiktok_publisher.py`, `test_publisher.py`, `test_tiktok_auth.py`): 101 passed. Full suite not required — no code changed.

**Milestone 2.0: COMPLETE.**
