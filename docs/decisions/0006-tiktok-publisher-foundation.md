# ADR-0006: TikTok Publisher Foundation

## Status

Accepted (corrected 2026-09-16 — see "Correction" note below)

**Correction note**: the original version of this ADR asserted that TikTok's OAuth does not support a useful localhost/loopback redirect flow for desktop apps, and built `tiktok_auth.py` around a fully manual two-command fallback as the *only* path. That assertion was wrong: TikTok's current Desktop Login Kit documentation supports `localhost`/`127.0.0.1` redirect URIs (including a wildcard port) for desktop apps, and requires PKCE for the desktop flow. This ADR and `tiktok_auth.py` were corrected to make the interactive PKCE + localhost-callback flow the preferred path, keeping the manual flow only as a fallback (also now PKCE + state-validated). This correction also tightened unaudited-client privacy handling (SELF_ONLY must be explicitly confirmed available, never silently substituted), added TikTok-bound caption-length validation (UTF-16 code units, not Python characters), and added creator-specific max-duration enforcement. The publisher architecture itself (three-table schema, `Publisher` interface, standalone CLI, idempotency rule) is unchanged by this correction.

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

`tiktok_publisher.py`'s `TikTokPublisher` is the only concrete implementation: queries `POST /v2/post/publish/creator_info/query/` for the account's actual `privacy_level_options` and `max_video_post_duration_sec` before ever publishing, then `POST /v2/post/publish/video/init/` (`source: "FILE_UPLOAD"`, since no object storage exists yet — see Non-Goals), `PUT`s the raw file bytes to the returned `upload_url`, and `POST /v2/post/publish/status/fetch/` to poll. All TikTok-specific request/response shapes live only in this module — `publish_tiktok.py` and `process_content.py` never see them.

**Unaudited-client privacy restriction.** This app has not completed TikTok's app review, and TikTok restricts unaudited Direct Post clients to `SELF_ONLY` (private) posts regardless of what other `privacy_level_options` an account's `creator_info` reports. `TikTokPublisher(unaudited=True)` — the default, and the only mode this milestone exercises — therefore does not simply check "is the requested level offered": it requires `privacy_level == "SELF_ONLY"` (a pure local check, refused before any network call if violated) *and* requires `"SELF_ONLY"` to actually appear in `creator_info`'s `privacy_level_options` (fails with `SELF_ONLY_UNAVAILABLE`, distinct from the older `UNSUPPORTED_PRIVACY_LEVEL`, if the account doesn't confirm it — an empty/absent options list is no longer treated as "no restriction"). `unaudited=False` is preserved as the pre-correction, more permissive check (requested level must appear in a non-empty options list; an empty/absent list is not validated) for a future audited client — this milestone never sets it.

**Caption length.** TikTok's Direct Post caption limit (2200) is defined in UTF-16 code units, not Python characters — a character outside the Basic Multilingual Plane (many emoji) is 1 Python character but a 2-unit UTF-16 surrogate pair, so `len(caption)` alone would undercount. `tiktok_publisher._utf16_length()` measures correctly (`len(text.encode("utf-16-le")) // 2`); `publish()` checks it first, before any network call, and fails with `CAPTION_TOO_LONG` rather than silently truncating — `videos.caption_text` stays canonical and untouched either way. This is the simpler of the two options considered (fail vs. derive a separate TikTok-bound truncated value); deriving a truncated version was deferred as unnecessary complexity for this milestone. A real Shofo video's transcript-derived caption (2522 units) tripped this validation during this milestone's own live verification — see Consequences.

**Duration.** `max_video_post_duration_sec` from `creator_info` is checked against the video's real duration, obtained by calling `media.inspect_media()` — the same ffprobe-based function `process_content.py` already uses — rather than introducing a second media-inspection path. A duration exceeding the account's reported limit fails with `VIDEO_TOO_LONG`; a limit that isn't reported at all is not treated as "reject everything."

### TikTok desktop OAuth: PKCE + localhost callback (preferred), manual fallback

`tiktok_auth.py` mirrors `calendar_manager.py`'s shape (cached-token-file, refresh-on-expiry, clear actionable errors) and, corrected from this ADR's original version, now also mirrors its *mechanism*: TikTok's current Desktop Login Kit documentation supports `localhost`/`127.0.0.1` redirect URIs — including a wildcard port — for desktop apps, and mandates PKCE for the desktop flow. The preferred path, `python3 tiktok_auth.py --authorize`, therefore:

1. generates a fresh, cryptographically random `state` (`secrets.token_urlsafe`) and a fresh PKCE `code_verifier`/`code_challenge` pair (S256) for this attempt only — never reused across attempts, never persisted to disk;
2. starts a temporary HTTP server on an OS-assigned localhost port (or the exact host/port from `TIKTOK_REDIRECT_URI` if that's explicitly configured, for an app registration that requires a fixed port rather than relying on wildcard-port matching);
3. prints (and tries to open) the authorization URL, including `code_challenge`/`code_challenge_method=S256`;
4. blocks for the single redirect;
5. verifies the returned `state` matches exactly (constant-time comparison) *before* doing anything else — a mismatch aborts with a clear error and nothing is exchanged or persisted;
6. exchanges the code, presenting `code_verifier`, and caches the resulting access/refresh token pair via `save_token()`.

A manual two-command fallback (`--print-auth-url` then `--exchange-code <code> --state <state>`) remains for environments where the interactive flow can't run (no local port binding, no browser at all) — corrected to also generate a fresh PKCE pair and validate `state` exactly, rather than skipping those requirements as the original manual-only implementation did. The pending `state`/`code_verifier`/`redirect_uri` are cached transiently at `TIKTOK_PENDING_AUTH_PATH` only between the two commands and deleted immediately by the second one, success or failure — never kept longer than that single attempt, unlike the long-lived token file.

Either flow caches the access/refresh token pair at `config.TIKTOK_TOKEN_PATH` (`~/.config/content-calendar/tiktok_token.json`, `0600` permissions where the platform supports it) — outside the repo, exactly like the Calendar OAuth token, so there is nothing publishing-credential-shaped to ever accidentally commit. `get_access_token()` refreshes transparently afterward using the cached refresh token; only the refresh token's own (≈365-day) expiry requires redoing the authorization flow.

TikTok credentials are entirely separate from Google Calendar's: different config variables (`TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`/`TIKTOK_REDIRECT_URI` vs. `CONTENT_CALENDAR_OAUTH_CLIENT_SECRETS`), different token cache file, different scopes, no shared code path. Missing/invalid TikTok credentials can never block Calendar operation or vice versa.

### `publish_tiktok.py`: standalone, not wired into any automation

`python3 publish_tiktok.py --video-id <id>` is the only way to trigger a publish in this milestone — never from `process_content.py`, never on a schedule, never in a background process. It: loads the video, checks for an existing `platform_posts` row, validates the local file exists and is TikTok-compatible (`media.is_tiktok_compatible`, reused rather than re-implemented) and has a stored `caption_text`, then calls `Publisher.publish()` and persists the result. `--poll-only` re-checks an existing in-flight submission's status without ever submitting.

### Idempotency logic

The decision rule, once a `platform_posts` row exists for `(video_id, "tiktok")`:

- **`platform_post_id` is set** (TikTok has accepted a submission, whatever happened next): never call `publish()` again, no matter what `status` currently reads — `PUBLISHING`, or even `FAILED` if TikTok later reported failure. Only `get_status()` is called. This is the "do not automatically retry an ambiguous API result as a brand-new post" requirement: once TikTok has an idea this video was submitted, a second submission would risk a real duplicate post, so the code refuses to create one under any circumstance short of a human deleting the row.
- **`platform_post_id` is `None`** (nothing was ever confirmed accepted — the local file was missing, credentials were invalid, the network call to `init` failed, or the upload itself failed): this is a genuine, safe retry target. A rerun reuses the existing row (`UPDATE`, never a second `INSERT` — the `UNIQUE` constraint would reject one anyway) and attempts submission again.

`platform_post_id` is persisted in its own `update_platform_post` call immediately after `publish()` returns, *before* the first `get_status()` poll — so a crash between submission and polling can never lose it; the next run sees it set and correctly falls into "poll only."

## Consequences

- **Not verified live in this environment**: no TikTok developer app, client credentials, or dedicated test account were available here, so the actual OAuth exchange, `creator_info`/`init`/upload/`status` calls, and the resulting private post have not been exercised against the real TikTok API — only against mocks matching the documented response shape, plus a real (non-mocked) localhost HTTP round trip proving the PKCE/state/callback mechanics themselves work correctly end to end. This mirrors ADR-0004's Google Calendar OAuth precedent (same reason: no real account access in this environment) — though unlike that precedent, the *mechanism* itself (loopback server, not just the token cache shape) is now implemented and locally verified; only the live TikTok-side exchange is unverified. The one-time TikTok Developer Portal app setup, granting this app's OAuth client a real registered redirect URI, and the first real `publish_tiktok.py --video-id <id>` run against the dedicated test account are the user's remaining manual steps — see README.md "TikTok Publishing Setup."
- A discovered, unrelated-but-blocking bug was fixed as a prerequisite: `process_content.py`'s `canonical_media_path` was set once at inspect time (pointing at the `content/incoming/` discovery path) and never updated when `_move_file` moved the file to `content/processed/` on assignment — so it went stale the instant a video was actually scheduled. Nothing in `process_content.py` itself depended on the stored value (it always re-resolves from the live discovery path), which is why this was silent until `publish_tiktok.py` needed to resolve a video's file location from stored state alone. Fixed by having `_move_file` return the actual destination path and folding it into the same `update_video` call that sets `status="ASSIGNED"`. The real database's 5 already-`ASSIGNED` Shofo videos were repaired to match (their files already existed in `content/processed/`; only the stored path was wrong).
- `requests` is now a direct runtime dependency (`requirements.txt`), not just a transitive one — `tiktok_auth.py`/`tiktok_publisher.py` import it directly for the HTTP calls Google's `googleapiclient`/`google-auth` stack doesn't cover.
- A `platform_posts` row can now exist in `FAILED` status with `platform_post_id=None` after a real attempt in an environment with no TikTok credentials configured, or (discovered during this correction pass's own live re-verification) with a caption that fails the new UTF-16 length check — real video 1's transcript-derived caption is 2522 UTF-16 units, over the 2200 limit. Neither is stuck: the next real run (once credentials exist, or once a shorter caption is stored) resubmits normally, per the idempotency rule above.

## Guardrails

- Never call `Publisher.publish()` for a `(video_id, platform)` that already has a non-null `platform_post_id` — check `content_store.get_platform_post()` first, always.
- Never add a second `platform` implementation (Instagram, YouTube) speculatively — `build_publisher`'s unsupported-platform error is intentional, not a gap to "complete."
- Never wire `publish_tiktok.py` into `process_content.py`, a cron job, or any background process in this milestone — that is explicitly the next milestone's decision, made with real requirements (retry/backoff policy, missed-schedule policy) in hand, exactly as ADR-0005 deferred `platform_posts` itself until this one.
- Never let TikTok credential/token state share a file, config variable, or code path with Google Calendar's OAuth.
- Never let `TikTokPublisher(unaudited=True)` (the default) accept or silently substitute a privacy level other than `SELF_ONLY`, and never treat an empty/absent `privacy_level_options` as "no restriction" in that mode — both require an explicit, confirmed `SELF_ONLY` before publishing.
- Never generate or reuse a PKCE `code_verifier`/`state` across more than one authorization attempt, and never skip verifying `state` before exchanging a code, in either the interactive or manual flow.
- Never silently truncate `videos.caption_text` to fit TikTok's limit — fail with `CAPTION_TOO_LONG` instead (this milestone's deliberate choice; see "Caption length" above).
- Keep TikTok-specific request/response shapes inside `tiktok_publisher.py` only.

## Current Implementation

- `content_store.py` — `SCHEMA_PLATFORM_POSTS`, `PlatformPostRecord`, `get_video`, `get_slot`, `get_platform_post`, `insert_platform_post`, `update_platform_post`.
- `publisher.py` — `Publisher`, `PublishError`, `PublishResult`, `PublishStatusResult`, `UnsupportedPlatformError`, `build_publisher`.
- `tiktok_auth.py` — `TikTokAuthError`, `generate_state`, `generate_pkce_pair`, `build_authorization_url`, `authorize_interactive` (preferred, PKCE + localhost callback), `start_manual_authorization`/`complete_manual_authorization` (manual fallback, also PKCE + state), `exchange_code_for_token`, `refresh_access_token`, `load_token`/`save_token`, `get_access_token`, and the `--authorize`/`--print-auth-url`/`--exchange-code` CLI.
- `tiktok_publisher.py` — `TikTokPublisher` (`query_creator_info`, `publish`, `get_status`, `unaudited` constructor flag), `_utf16_length`.
- `publish_tiktok.py` — the standalone manual CLI (`publish_video`, `_poll_and_update`, idempotency logic).
- `config.py` — `TIKTOK_AUTHORIZE_BASE`, `TIKTOK_API_BASE`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` (optional — only needed for a fixed-port registration or the manual fallback), `TIKTOK_LOOPBACK_HOST`, `TIKTOK_LOOPBACK_PATH`, `TIKTOK_SCOPES`, `TIKTOK_TOKEN_PATH`, `TIKTOK_DEFAULT_PRIVACY_LEVEL`, `TIKTOK_MAX_CAPTION_UTF16_UNITS`.
- `process_content.py` — `_move_file` now returns the destination path; `canonical_media_path` updated on assignment (the prerequisite fix above).
- Tests: `tests/test_publisher.py`, `tests/test_tiktok_auth.py` (including a real, non-mocked localhost HTTP round trip for the interactive flow), `tests/test_tiktok_publisher.py`, `tests/test_publish_tiktok.py`, additions to `tests/test_content_store.py` and `tests/test_fifo_process_content.py`. All TikTok *network* calls are mocked — no real account required for the suite; the loopback server itself is real (same-machine only).
