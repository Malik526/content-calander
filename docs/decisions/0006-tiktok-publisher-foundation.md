# ADR-0006: TikTok Publisher Foundation

## Status

Accepted

## Context

Through Milestone 1.3.1, this pipeline proved real video → media inspection → transcription → transcript-derived caption → FIFO assignment → SQLite persistence → Google Calendar scheduling, all against real Shofo MP4s. Nothing in the pipeline had ever actually posted anywhere — `videos`/`content_slots` deliberately stayed 1:1 (see ADR-0005, "No `platform_posts` table... a `platform_posts` table today would be exactly the large generalized publishing framework in anticipation of future platforms this milestone was explicitly told to avoid... When TikTok publishing lands, a `platform_posts` table is that milestone's decision to make with real requirements in hand").

This is that milestone. The goal is narrow and deliberately so: prove `one known local MP4 → connected TikTok test account → TikTok API → private post → poll/check result → persist publishing result` for exactly one manually-chosen video, before building any scheduler, worker, retry engine, or additional platform around it.

## Decision

### Schema: `platform_posts`, not new columns on `videos`

`content_store.SCHEMA_PLATFORM_POSTS` adds one new table:

```sql
CREATE TABLE platform_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    platform_post_id TEXT,
    scheduled_at TEXT,
    published_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(video_id, platform)
);
```

The three-table separation from ADR-0005 is preserved exactly: `videos` is canonical content/media metadata, `content_slots` is scheduling assignment, `platform_posts` is external publishing state/result. A video will eventually have zero or more `platform_posts` rows (one per platform) — this could never have been a 1:1 column addition to `videos` even for a single platform, and cramming TikTok-specific state into `videos` was explicitly ruled out.

`UNIQUE(video_id, platform)` is the idempotency primitive, enforced by the schema rather than only by caller discipline: a video can have at most one publishing record per platform, so `publish_tiktok.py` cannot accidentally create a second row for the same video even if it forgot to check first — the `INSERT` would raise `sqlite3.IntegrityError`.

Statuses are deliberately minimal: `PENDING` (record exists, no submission attempted yet or a true submission failure with no `platform_post_id`), `PUBLISHING` (a `platform_post_id` has been obtained; TikTok is still processing), `PUBLISHED` (terminal success), `FAILED` (terminal failure — either a submission never reached TikTok, or TikTok accepted it and later reported failure). No richer state machine (`SCHEDULED`/`READY_TO_PUBLISH`/queued/etc.) — that's the next milestone's decision once real scheduler requirements exist, exactly as ADR-0005 deferred this table itself.

`platform_posts` is pure additive schema (`CREATE TABLE IF NOT EXISTS`, wired into `ContentStore.__init__` alongside the existing `videos`/`content_slots` setup) — no rebuild/migration was needed for the table itself. It does, however, inherit the cross-table FK-rewrite hazard documented under "Migration Safety" in `PROJECT_STATE.md`: `platform_posts.video_id REFERENCES videos(id)` would be silently corrupted to reference `videos_old` by a naive `videos` rebuild, the same way `content_slots.assigned_video_id` was. `_repair_videos_assigned_slot_fk`'s existing `PRAGMA legacy_alter_table = ON` guard already prevents this for *any* sibling table, `platform_posts` included — verified with a dedicated regression test (`test_videos_fk_repair_preserves_platform_posts_fk`) rather than merely assumed to extend.

### Publisher interface + TikTokPublisher, mirroring the existing Transcriber/ContentClassifier pattern

`publisher.py` defines `Publisher` (ABC: `publish(video_path, caption) -> PublishResult`, `get_status(platform_post_id) -> PublishStatusResult`), `PublishError` (carries a machine-stable `reason_code`), and `build_publisher(platform, **kwargs)` — the same interface-plus-factory shape as `transcription.Transcriber`/`classification.ContentClassifier`+`build_classifier()`. Only `"tiktok"` is registered; requesting anything else raises `UnsupportedPlatformError` immediately, mirroring `build_classifier`'s unsupported-value message style. Instagram/YouTube are explicitly not stubbed — per this milestone's own scope, and because a second implementation with no real requirements yet would just be speculative shape-guessing.

`tiktok_publisher.py`'s `TikTokPublisher` is the only concrete implementation: queries `POST /v2/post/publish/creator_info/query/` for the account's actual `privacy_level_options` before ever publishing (never assumes `SELF_ONLY` is available — fails clearly with `UNSUPPORTED_PRIVACY_LEVEL` if the account doesn't offer it), then `POST /v2/post/publish/video/init/` (`source: "FILE_UPLOAD"`, since no object storage exists yet — see Non-Goals), `PUT`s the raw file bytes to the returned `upload_url`, and `POST /v2/post/publish/status/fetch/` to poll. All TikTok-specific request/response shapes live only in this module — `publish_tiktok.py` and `process_content.py` never see them.

**Explicit caveat**: this request/response shape is implemented from TikTok's public Content Posting API v2 documentation, not verified against a live call in this environment — no TikTok developer app or test-account credentials were available here (mirroring exactly how ADR-0004's Google Calendar OAuth flow could not be exercised live, for the same reason: no browser, no real account access). Automated tests mock every network call; the one live integration test is the user's manual step (see Consequences).

### TikTok OAuth is a separate, manual flow — deliberately not automated around

`tiktok_auth.py` mirrors `calendar_manager.py`'s shape (cached-token-file, refresh-on-expiry, clear actionable errors) but not its mechanism: `calendar_manager.build_oauth_calendar_service` uses `google-auth-oauthlib`'s `InstalledAppFlow.run_local_server()`, a loopback flow TikTok's OAuth does not support in the general case (the `redirect_uri` must exactly match one pre-registered in the TikTok Developer Portal, which this project cannot stand up or register automatically). Rather than build a fragile approximation, authorization is a deliberately manual two-command flow: `python3 tiktok_auth.py --print-auth-url` prints the consent URL; after approving in a browser as the dedicated test account, `python3 tiktok_auth.py --exchange-code <code>` (copied from the browser's address bar after the redirect) completes it and caches the access/refresh token pair at `config.TIKTOK_TOKEN_PATH` (`~/.config/content-calendar/tiktok_token.json`, default `0600` permissions where the platform supports it) — outside the repo, exactly like the Calendar OAuth token, so there is nothing publishing-credential-shaped to ever accidentally commit. `get_access_token()` refreshes transparently afterward using the cached refresh token; only the refresh token's own (≈365-day) expiry requires redoing the manual flow.

TikTok credentials are entirely separate from Google Calendar's: different config variables (`TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`/`TIKTOK_REDIRECT_URI` vs. `CONTENT_CALENDAR_OAUTH_CLIENT_SECRETS`), different token cache file, different scopes, no shared code path. Missing/invalid TikTok credentials can never block Calendar operation or vice versa.

### `publish_tiktok.py`: standalone, not wired into any automation

`python3 publish_tiktok.py --video-id <id>` is the only way to trigger a publish in this milestone — never from `process_content.py`, never on a schedule, never in a background process. It: loads the video, checks for an existing `platform_posts` row, validates the local file exists and is TikTok-compatible (`media.is_tiktok_compatible`, reused rather than re-implemented) and has a stored `caption_text`, then calls `Publisher.publish()` and persists the result. `--poll-only` re-checks an existing in-flight submission's status without ever submitting.

### Idempotency logic

The decision rule, once a `platform_posts` row exists for `(video_id, "tiktok")`:

- **`platform_post_id` is set** (TikTok has accepted a submission, whatever happened next): never call `publish()` again, no matter what `status` currently reads — `PUBLISHING`, or even `FAILED` if TikTok later reported failure. Only `get_status()` is called. This is the "do not automatically retry an ambiguous API result as a brand-new post" requirement: once TikTok has an idea this video was submitted, a second submission would risk a real duplicate post, so the code refuses to create one under any circumstance short of a human deleting the row.
- **`platform_post_id` is `None`** (nothing was ever confirmed accepted — the local file was missing, credentials were invalid, the network call to `init` failed, or the upload itself failed): this is a genuine, safe retry target. A rerun reuses the existing row (`UPDATE`, never a second `INSERT` — the `UNIQUE` constraint would reject one anyway) and attempts submission again.

`platform_post_id` is persisted in its own `update_platform_post` call immediately after `publish()` returns, *before* the first `get_status()` poll — so a crash between submission and polling can never lose it; the next run sees it set and correctly falls into "poll only."

## Consequences

- **Not verified live in this environment**: no TikTok developer app, client credentials, or dedicated test account were available here, so the actual OAuth exchange, `creator_info`/`init`/upload/`status` calls, and the resulting private post have not been exercised against the real TikTok API — only against mocks matching the documented response shape. This mirrors ADR-0004's Google Calendar OAuth precedent exactly (same reason: no browser, no real account access in this environment). The one-time TikTok Developer Portal app setup, the manual `tiktok_auth.py` authorization flow, and the first real `publish_tiktok.py --video-id <id>` run against the dedicated test account are the user's remaining manual steps — see README.md "TikTok Publishing Setup."
- A discovered, unrelated-but-blocking bug was fixed as a prerequisite: `process_content.py`'s `canonical_media_path` was set once at inspect time (pointing at the `content/incoming/` discovery path) and never updated when `_move_file` moved the file to `content/processed/` on assignment — so it went stale the instant a video was actually scheduled. Nothing in `process_content.py` itself depended on the stored value (it always re-resolves from the live discovery path), which is why this was silent until `publish_tiktok.py` needed to resolve a video's file location from stored state alone. Fixed by having `_move_file` return the actual destination path and folding it into the same `update_video` call that sets `status="ASSIGNED"`. The real database's 5 already-`ASSIGNED` Shofo videos were repaired to match (their files already existed in `content/processed/`; only the stored path was wrong).
- `requests` is now a direct runtime dependency (`requirements.txt`), not just a transitive one — `tiktok_auth.py`/`tiktok_publisher.py` import it directly for the HTTP calls Google's `googleapiclient`/`google-auth` stack doesn't cover.
- A `platform_posts` row can now exist in `FAILED` status with `platform_post_id=None` after a real attempt in an environment with no TikTok credentials configured (exactly what happened when this milestone's own real-database check was run) — this is not stuck; the next real run (once credentials exist) resubmits it normally, per the idempotency rule above.

## Guardrails

- Never call `Publisher.publish()` for a `(video_id, platform)` that already has a non-null `platform_post_id` — check `content_store.get_platform_post()` first, always.
- Never add a second `platform` implementation (Instagram, YouTube) speculatively — `build_publisher`'s unsupported-platform error is intentional, not a gap to "complete."
- Never wire `publish_tiktok.py` into `process_content.py`, a cron job, or any background process in this milestone — that is explicitly the next milestone's decision, made with real requirements (retry/backoff policy, missed-schedule policy) in hand, exactly as ADR-0005 deferred `platform_posts` itself until this one.
- Never let TikTok credential/token state share a file, config variable, or code path with Google Calendar's OAuth.
- Never assume `SELF_ONLY` (or any privacy level) is available for an account without checking `creator_info` first.
- Keep TikTok-specific request/response shapes inside `tiktok_publisher.py` only.

## Current Implementation

- `content_store.py` — `SCHEMA_PLATFORM_POSTS`, `PlatformPostRecord`, `get_video`, `get_slot`, `get_platform_post`, `insert_platform_post`, `update_platform_post`.
- `publisher.py` — `Publisher`, `PublishError`, `PublishResult`, `PublishStatusResult`, `UnsupportedPlatformError`, `build_publisher`.
- `tiktok_auth.py` — `TikTokAuthError`, `build_authorization_url`, `exchange_code_for_token`, `refresh_access_token`, `load_token`/`save_token`, `get_access_token`, and the `--print-auth-url`/`--exchange-code` CLI.
- `tiktok_publisher.py` — `TikTokPublisher` (`query_creator_info`, `publish`, `get_status`).
- `publish_tiktok.py` — the standalone manual CLI (`publish_video`, `_poll_and_update`, idempotency logic).
- `config.py` — `TIKTOK_AUTHORIZE_BASE`, `TIKTOK_API_BASE`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`, `TIKTOK_SCOPES`, `TIKTOK_TOKEN_PATH`, `TIKTOK_DEFAULT_PRIVACY_LEVEL`.
- `process_content.py` — `_move_file` now returns the destination path; `canonical_media_path` updated on assignment (the prerequisite fix above).
- Tests: `tests/test_publisher.py`, `tests/test_tiktok_auth.py`, `tests/test_tiktok_publisher.py`, `tests/test_publish_tiktok.py`, additions to `tests/test_content_store.py` and `tests/test_fifo_process_content.py`. All TikTok network calls mocked — no real account required for the suite.
