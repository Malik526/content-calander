# Milestone 3.7 — Batch Upload UX: Readiness Check + Minimum Implementation

## Objective

Prepare Pickle Batch for the real-user Batch Upload UX validation: an authenticated user selects
multiple finished short-form videos, uploads them through the real hosted backend, each one is
stored in the existing canonical Supabase Storage structure and gets an owned Postgres `videos`
row, and the real uploaded videos show up in `/app/library`. Deliberately excludes transcription,
scheduling, caption generation, publishing, and worker logic — this milestone's own scope
guardrail, matching AGENTS.md's Milestone 3 scope boundaries.

This record covers a readiness/implementation pass, not the live validation itself — the real
end-to-end test (the user's own real video files, through the real deployed Railway+Netlify
stack) is still outstanding; see "Manual Validation Steps" below.

## Phase 1 — Inspection

Read first: `AGENTS.md`, `docs/architecture/hosted-product-boundary.md` (§4 API sync/async
boundary, §5 background-job boundaries, the "Upload flow"/local-filesystem-assumptions
sections), `docs/decisions/0009-object-storage-media-lifecycle.md`.

Found:

- **Storage abstraction**: `storage.protocol.StorageProtocol` (put/exists/delete/materialize),
  `storage.local.LocalStorage`, `storage.supabase_storage.SupabaseStorage`,
  `storage.factory.build_storage()` — all real, tested (Milestone 3.4), but **not yet called from
  any live entry point** (CLI or API) before this milestone.
- **Video/DB model**: `videos` has `user_id`/`storage_provider`/`storage_key` on both SQLite and
  Postgres. `media.media_storage.upload_canonical_media()` bridges an *existing* video row (one
  already created by local CLI ingestion, with a real local `canonical_media_path`) to object
  storage — it has no path for creating a brand-new video row directly from freshly-received
  bytes with no prior local-ingestion step. Nothing in the codebase did that.
- **API surface**: only `GET /api/me` and `/api/platforms/tiktok/{status,connect,callback,disconnect}`
  existed. No `/api/videos*` routes at all.
- **Library UI**: `web/app/app/library/page.tsx` was a static shell — no data fetching, an inert
  "Upload coming soon" button. Not wired to anything real.
- **Frontend API client**: `web/lib/api/client.ts`'s `apiRequest()` always JSON-serialized the
  body and always set `Content-Type: application/json` — no multipart/file-upload support.
- **Ownership/tenant isolation**: enforced at the API layer via `get_current_user`
  (`api/dependencies/auth.py`) and, for media specifically, `media_storage`'s
  `MediaOwnershipError` check-before-act pattern. Real and consistent; the new upload path needed
  to follow the same convention, not invent a new one.
- **A real pre-existing constraint the new feature had to handle, not fix**: `videos.file_hash`
  is `NOT NULL UNIQUE` **globally**, not scoped per user, on both SQLite and Postgres (predates
  Milestone 3.2 ownership). Two different users uploading byte-identical content collide at the
  schema level. Rescoping this to `UNIQUE(user_id, file_hash)` would be a real schema change —
  explicitly out of this milestone's scope (AGENTS.md's "Coding/Refactor Rules" +
  "Scope Guardrails": no database schema redesign unless a task explicitly asks). Handled at the
  application layer instead — see `DuplicateVideoContentError` below.
- `python-multipart` (required by FastAPI/Starlette to parse `multipart/form-data` — i.e. any
  `UploadFile`) was **not installed** and not in `requirements.txt`. Any file-upload endpoint
  would have failed at runtime without it.

## Phase 2 — Implementation

Backend:

- `persistence/protocol.py` — added `insert_video`/`list_videos_for_user` to
  `ContentStoreProtocol` (documentation-as-code, matching the existing convention).
- `persistence/content_store.py` / `persistence/postgres_content_store.py` — added
  `list_videos_for_user(user_id)`, scoped strictly by `user_id`, newest first.
- `media/media_storage.py` — added `create_video_from_upload()`: the hosted-upload counterpart
  to `upload_canonical_media()`. Given already-received bytes at a temp local path, it creates a
  new owned `videos` row (`canonical_media_path` deliberately left `NULL` — the temp file does not
  survive the request, unlike local ingestion's permanently-retained copy — `storage_provider`/
  `storage_key` are the row's only reference, exactly the shape `materialize_canonical_media`
  already knows how to resolve), uploads it via the injected `StorageProtocol`, and is idempotent
  per (user, exact content). Raises the new `DuplicateVideoContentError` when the content already
  belongs to a *different* user — the caller never learns which other account/video it collided
  with.
- `api/dependencies/storage.py` (new) — `get_storage()`, mirroring `get_store()` exactly.
- `api/schemas/videos.py` (new) — `VideoResponse`/`VideoListResponse`/`VideoUploadResult`/
  `VideoUploadBatchResponse`. Deliberately excludes `storage_key`/`storage_provider`/
  `canonical_media_path`/`original_path` — same "don't surface an opaque internal identifier with
  no user value" lesson as the Milestone 3.6 security review's TikTok `open_id` finding.
- `api/routes/videos.py` (new) — `POST /api/videos` (batch upload, one multipart request, one
  `VideoUploadResult` per file — a bad file never aborts the rest of the batch) and
  `GET /api/videos` (the caller's own videos, newest first). Deliberately a plain `def` route, not
  `async def`, matching every other route in this API: this endpoint does real blocking I/O
  (`SupabaseStorage.put`'s network call; SQLite's connection is only safe to use from a single
  thread at a time), and mixing that with `UploadFile`'s async `.read()` API risked handing the
  same SQLite connection to two different worker threads across two independently-dispatched
  `run_in_threadpool` calls — caught and fixed during implementation, not shipped. Reads each
  upload via `UploadFile.file` (its underlying sync file object) instead.
- `requirements.txt` — added `python-multipart>=0.0.9` (installed locally; **must reach Railway
  on the next deploy** — see "Remaining Blockers").
- `api/app.py` — wired the new router in; updated its own stale module docstring (previously
  claimed "no batch-upload... endpoints" as this milestone's boundary).

Frontend:

- `web/lib/api/client.ts` — `apiRequest()` now passes a `FormData` body straight through
  (never `JSON.stringify`'d) and omits a manual `Content-Type` for it, letting the browser set its
  own multipart boundary. Every existing JSON caller is unaffected.
- `web/lib/api/types.ts` — added `VideoResponse`/`VideoListResponse`/`VideoUploadResult`/
  `VideoUploadBatchResponse` (mirrors the backend's actual JSON, same convention as
  `CurrentUser`/`TikTokConnectionStatus`). The pre-existing mock-only `VideoSummary` type and
  `lib/api/mockData.ts` are untouched — unrelated to this milestone (Queue's own future scope).
- `web/lib/api/videos.ts` (new) — `listVideos`/`uploadVideos` typed calls.
- `web/components/forms/VideoUploadForm.tsx` (new) — select multiple files (a distinct step from
  upload — a wrong pick can be cleared first), upload as one batch request, show a per-file
  success/failure result list.
- `web/app/app/library/page.tsx` — rewritten from the static shell to a real client component:
  loads the current user's videos on mount, renders the upload form above the list, refreshes the
  list after a batch completes. No `accessToken` (the dev-mock-session fallback) means there is no
  real backend to call at all — treated as the same "no videos yet" empty state a genuinely empty
  account would show, not a perpetual spinner, since that is the honest outcome either way.

## Tests

Backend (`env -u DATABASE_URL .venv/bin/python3 -m pytest -q`): **777 passed** (759 baseline + 18
new — `tests/test_api_videos.py` (12: auth required, single/batch upload success, unsupported
file type doesn't abort the batch, idempotent re-upload by the same user, duplicate content from
a different user fails cleanly without naming the other account, Library listing requires auth,
empty/populated/tenant-isolated listing, no internal identifiers leaked to the client),
`tests/test_media_storage.py` (+4: `create_video_from_upload`'s row/storage creation, idempotency,
cross-user duplicate rejection, never touches the caller's own temp file), `tests/test_content_store.py`
(+3: `list_videos_for_user` scoping/ordering/empty-result/never-leaks-legacy-unowned-rows)). Zero
existing tests modified or weakened.

Also added `tests/test_postgres_content_store.py::test_list_videos_for_user_scopes_strictly_by_user_newest_first`
for schema parity on the real Postgres backend — **not run in this environment** (this sandbox's
own production-access safeguard blocks any pytest invocation with `DATABASE_URL` set at all, even
though this specific test only ever touches the disposable `POSTGRES_TEST_SCHEMA`, never `public`
— consistent with how every other Postgres-integration test in this suite already behaves). It
will run wherever `DATABASE_URL` is configured normally (CI, or a future session with that
permission).

Frontend (`npm run test -- --run`, `npm run lint`, `npm run build`, all from `web/`): **81 passed**
(67 baseline + 14 new — `tests/lib/videos.test.ts` (4), `tests/routes/library.test.tsx` (5), 2 new
`FormData`-handling cases in `tests/lib/client.test.ts`, `tests/routes/app-routes.test.tsx`'s
Library case updated (not removed) to wrap in `SessionProvider` now that the page is a real client
component). Lint clean. Production build clean (all 10 routes, including `/app/library`).

## Manual Validation Steps (with your own real videos)

1. Make sure the next Railway deploy has picked up `requirements.txt`'s new `python-multipart`
   dependency (redeploy if the currently-running instance predates this change — see "Remaining
   Blockers").
2. Open the deployed frontend, sign in with Google, go to **Library**.
3. Under "Upload videos," click the file input and select 2–3 real finished video files
   (`.mp4`/`.mov`).
4. Click **Upload N videos**. Confirm each file shows a ✓ result line.
5. Confirm the videos now appear in the list below, with their real filenames and file sizes.
6. Reload the page — confirm the same videos are still there (proves they persisted, not just
   client-side state).
7. Try uploading one unsupported file type (e.g. a `.txt` or `.jpg`) alongside a real video in the
   same batch — confirm the video still succeeds and the bad file shows a clear ✗ error, not a
   failed whole request.
8. Try uploading the exact same video file a second time — confirm it does not create a
   duplicate row in the list (idempotent re-upload).
9. If you have a second real account to test with, confirm that account's Library never shows the
   first account's videos.

## Remaining Blockers Before Live 3.7 Validation

- **`python-multipart` must reach the real Railway deployment.** It's in `requirements.txt` now,
  but Railway's build (`pip install -r requirements.txt && pip install .`) only picks it up on the
  *next* deploy — if the currently-running instance was built before this change, step 3 above
  will fail with a 500 until it's redeployed.
- **Not yet exercised against real Supabase Storage or real production Postgres from this
  environment** — every test above runs against `LocalStorage`/a temp-file SQLite DB or the
  disposable Postgres test schema, per this repository's own testing conventions (no live
  TikTok/Calendar/DB calls from automated tests). The real `SupabaseStorage.put()` HTTP call path
  is unchanged code (Milestone 3.4, already tested against real Supabase separately) but has never
  been driven by this new upload endpoint specifically until a real user does step 3.
- Everything else (ffprobe/duration/dimensions display, transcription, scheduling, captions,
  publishing) is deliberately **not** built — out of this milestone's scope by design, not an
  oversight.

## Overall

**Milestone 3.7 readiness work is COMPLETE; the milestone itself remains IN PROGRESS** pending the
live validation above with real video files against the real deployed stack — matching this
repository's own established pattern (see Milestone 3.6's own addenda) of not marking a milestone
COMPLETE until a real user has exercised the real hosted path end to end.

## Addendum (2026-09-26) — Data Quality + Upload Instrumentation Follow-up

Triggered by the first real hosted uploads and a direct DB inspection, which found
`storage_provider="local"` on rows whose `storage_key` looked Supabase-shaped.

**Root cause of `storage_provider="local"`:** not a code defect. Every write site
(`create_video_from_upload`, `upload_canonical_media`) stamps `storage_provider` from the
*actually-injected* `StorageProtocol.provider_name` — never a literal string (re-verified by
grep and by a new stub-backend test, `test_create_video_from_upload_records_the_actual_injected_backend_not_a_hardcoded_string`,
proving the code would record `"supabase"` given a Supabase-shaped backend). The real cause:
`config.STORAGE_BACKEND` defaults to `"local"` when unset (a deliberate, pre-existing default —
see ADR-0009's own "Consequences" section, predating this milestone), and `build_storage()` had
never been called from any live entry point until this milestone's upload endpoint — so if
Railway's dashboard was never explicitly given `STORAGE_BACKEND=supabase`, `LocalStorage` is what
really ran, and the uploaded bytes are sitting under that process's `LOCAL_STORAGE_ROOT`, not in
the real Supabase bucket. A `storage_key` shaped like a Supabase path proves nothing about which
backend actually holds the bytes — `build_storage_key()` is deliberately backend-agnostic. Full
detail in ADR-0009's own 2026-09-26 addendum. **Action needed (operational, not code):** confirm/
set `STORAGE_BACKEND=supabase` (plus `SUPABASE_URL`/`SERVICE_ROLE_KEY`/`SUPABASE_STORAGE_BUCKET`)
on Railway's dashboard, then redeploy. `api/app.py`'s startup diagnostic now also logs
`STORAGE_BACKEND` so this is visible in Railway's logs on the very next deploy.

**`canonical_media_path` decision:** confirmed intentional, not a gap — `create_video_from_upload`
already left it `NULL` for hosted uploads (see its own docstring, written when it was built) and
this follow-up formalizes that in ADR-0009 itself: `storage_key` (paired with `storage_provider`)
is the one canonical hosted-media identifier; a hosted upload never had a local file to record
"lineage" for, unlike the local-ingestion-then-migrated case ADR-0009 originally described. No
code change — documentation only.

**Upload performance instrumentation:** added event/attempt-level tables, not columns on
`videos` — `upload_batches` (one row per `POST /api/videos` request: `user_id`, `started_at`,
`completed_at`, `file_count`, `total_bytes`, `total_duration_ms`, `status`) and `upload_attempts`
(one row per file: `batch_id`, `user_id`, `video_id` nullable, `original_filename`,
`file_size_bytes`, `started_at`, `completed_at`, `duration_ms`, `status`, `error_code` nullable) —
on both backends (`persistence/content_store.py`, `persistence/postgres_content_store.py`,
`postgres_migrations/0004_add_upload_telemetry.sql`), with `create_*`/`update_*`/
`list_upload_batches_for_user`/`get_upload_attempts_for_batch` accessors added to
`ContentStoreProtocol`. Wired into `api/routes/videos.py`: one batch row per request, one attempt
row per file (including files rejected for an unsupported extension — `error_code="UNSUPPORTED_FILE_TYPE"`
— and duplicate-content rejections — `error_code="DUPLICATE_CONTENT"`, reused from
`DuplicateVideoContentError.reason_code`), successful attempts carry the resulting `video_id`.
Throughput (bytes/sec) is deliberately **not** its own stored column — derive it from
`total_bytes / total_duration_ms` at query time, per the brief's own "keep stored telemetry
minimal" framing; a derived ratio is not durable state worth persisting redundantly.

**Timing boundaries measured, and what they are not:** every `duration_ms` (both tables) is a
server-side `time.monotonic()` delta (never wall-clock subtraction, which a clock adjustment
mid-request could corrupt) — `upload_attempts.duration_ms` spans from just before this process
starts streaming one file's bytes to a temp file through hashing + `storage.put` + the DB write;
`upload_batches.total_duration_ms` spans the whole request. **Neither is labeled or should be read
as "network latency" or "upload speed"** — by the time this route function runs at all, uvicorn/
Starlette has already fully received the client's multipart body; this server has no way to
observe the client's actual transfer time. This distinction is documented directly in
`api/routes/videos.py`'s own module docstring so it can't be misread later. Client-side (true
network + queue) timing is not captured at all in this pass — would require either browser RUM or
a client-reported timing field, both deliberately deferred as beyond "minimal."

**Media intelligence roadmap (documented, not built):** `media/media_storage.py`'s module
docstring now spells out exactly which fields a future background job could populate via the
existing `media.inspection.inspect_media` (container, video_codec, audio_codec, width, height,
fps, duration_seconds) and recommends where it belongs — a plain interval-triggered job over
"videos with storage_provider set and container IS NULL" (matching
`hosted-product-boundary.md` §5's existing job-boundary shape), never synchronous in the upload
request (§4 already rules that out for ffprobe). transcript/classification/caption/scheduling
remain a separate, later concern. No code added — this pass's own scope guardrail.

**Navigation-safe uploads: implemented.** A small app-level provider, `lib/uploads.tsx`
(`UploadProvider`/`useUploadManager()`), now owns `uploading`/`lastResults`/`error` — mounted in
`app/app/layout.tsx` *above* the per-route page content, exactly like `SessionProvider` already
is, so it persists across every in-app `/app/*` navigation. Investigated first: the underlying
`fetch()` call was never actually cancelled by navigating away (a browser request isn't tied to
React's component tree) — the real gap was that the *feedback* (in-progress/result state) lived
only in `VideoUploadForm`'s local `useState`, so navigating away and back made a real, still-
running or already-finished upload look like it had vanished. `VideoUploadForm`/`LibraryPage` now
read shared state via `useUploadManager()`; `LibraryPage` refreshes its list whenever `uploading`
transitions from true to false while it happens to be mounted, and does its own fresh fetch on
every mount regardless — so a batch that completes while the user is on Queue/Settings is picked
up correctly when they return to Library. Browser-tab-close/resumable-upload support remains
explicitly out of scope, per the brief.

**Tests:** backend 783 passed (777 Milestone-3.7-readiness baseline + 6 net new, verified directly
via `pytest --collect-only` before/after — `tests/test_api_videos.py` (4: batch/attempt
persistence, success/failure timing records, tenant isolation of telemetry, duplicate-content
error-code recording) and `tests/test_media_storage.py` (1: the stub-backend `storage_provider`
proof); `tests/test_postgres_content_store.py` gained a parity test, not run in this sandboxed
environment for the same production-DB-access reason as before). Frontend 86 passed (81 baseline +
5 new — `tests/lib/uploads.test.tsx`, a navigation-survival test in `tests/routes/library.test.tsx`,
`tests/routes/app-routes.test.tsx` updated for the new `UploadProvider` wrapper requirement), lint
clean, build clean. Zero existing tests weakened;
`tests/routes/library.test.tsx`'s one changed assertion (a failed upload now also triggers a
list refresh, not just a successful one) reflects a deliberate, documented design simplification,
not a regression.

**Files changed:** `src/content_automation/persistence/{content_store,postgres_content_store,protocol}.py`,
`src/content_automation/persistence/postgres_migrations/0004_add_upload_telemetry.sql`,
`src/content_automation/media/media_storage.py`, `src/content_automation/api/routes/videos.py`,
`src/content_automation/api/app.py`, `docs/decisions/0009-object-storage-media-lifecycle.md`,
`tests/test_api_videos.py`, `tests/test_media_storage.py`, `tests/test_content_store.py`,
`tests/test_postgres_content_store.py`, `web/lib/uploads.tsx` (new), `web/components/forms/VideoUploadForm.tsx`,
`web/app/app/library/page.tsx`, `web/app/app/layout.tsx`, `web/tests/lib/uploads.test.tsx` (new),
`web/tests/routes/library.test.tsx`, `web/tests/routes/app-routes.test.tsx`.

**Remains intentionally deferred:** ffprobe/media-metadata enrichment (documented, not built —
see above), client-side upload timing, throughput as a stored column, transcription/scheduling/
captioning/publishing/worker architecture (this pass's own explicit exclusion), browser-close/
resumable upload support, and any UI to browse `upload_batches`/`upload_attempts` (this pass built
persistence only — no API/UI surface for telemetry, since nothing asked for one yet).

**Exact live validation steps for the next real batch:**
1. Confirm/set `STORAGE_BACKEND=supabase` (and `SUPABASE_URL`/`SERVICE_ROLE_KEY`/
   `SUPABASE_STORAGE_BUCKET`) on Railway's dashboard, then trigger a redeploy so both this and the
   earlier `python-multipart` fix are live.
2. Upload a small batch of real videos through the deployed frontend, same as before.
3. Check Railway's logs for the startup line — confirm `STORAGE_BACKEND: 'supabase'`.
4. Check the Supabase Storage dashboard directly for the real bucket — confirm the uploaded
   objects are actually there (not just that the app returned success).
5. Query `videos` for those rows — confirm `storage_provider='supabase'` this time, and
   `canonical_media_path IS NULL`.
6. Query `upload_batches`/`upload_attempts` for the same batch — confirm one batch row, one
   attempt row per file, `status`/`duration_ms`/`file_size_bytes`/`video_id` all populated
   sensibly, and that a deliberately-included unsupported file (if you include one) shows
   `error_code='UNSUPPORTED_FILE_TYPE'` with `video_id` still `NULL`.
7. Confirm Library still shows the uploaded videos correctly (no behavior change expected here).
8. Start an upload, then click to Queue or Settings mid-upload, then back to Library — confirm
   the result still shows up correctly rather than looking like it vanished.

Milestone 3.7 remains **IN PROGRESS** — this follow-up's own instrumentation and corrected
metadata are not yet validated against a real hosted upload themselves; that is exactly what the
steps above are for.

## Addendum (2026-09-26) — Delete Video

Added the Library's first destructive action.

**Queue/schedule-reference check:** `media.media_storage.delete_video(store, storage, video_id,
user_id)` refuses (raises `VideoHasScheduleReferencesError`) without touching anything if the
video is still assigned to a `content_slot` (`videos.assigned_slot_id IS NOT NULL`) or has any
`platform_posts` row for any platform, in any status — pending, published, or failed. This is a
deliberate over-inclusion: even a *published* `platform_posts` row blocks deletion, on the
reasoning that erasing the video record behind a real publish history is exactly the kind of
silent-cascade the brief said to avoid, and no milestone-3.7-scoped UI exists yet to let a user
resolve that state (unassign a slot, remove a post) — that remains a backend/CLI-side-only fix
for now, explicitly deferred rather than built here.

**When safe to delete:** the stored object is removed via the same `StorageProtocol` instance the
upload path itself uses (`storage.delete(video.storage_key)`), then the `videos` row is deleted.
A legacy local-only video (pre-3.4, `storage_provider`/`storage_key` both `NULL`) has nothing for
`StorageProtocol` to delete — that step is skipped entirely, and `canonical_media_path`/the local
file on disk are never touched, matching ADR-0009's "Retention" decision. Proven directly with a
storage stub that raises if `delete()` is ever called for such a video
(`test_delete_video_never_touches_a_legacy_local_video_with_no_storage_key`).

**`canonical_media_path` / two-columns concern:** not applicable here — no new column was added;
this reuses the existing `storage_key`/`storage_provider`/`canonical_media_path` shape exactly as
the prior addendum settled it.

**Upload telemetry cleanup:** `upload_attempts.video_id` (nullable specifically for this) is set
to `NULL` for every attempt that pointed at the deleted video — the row itself (filename, size,
duration, status, error_code) is kept, not deleted, so historical upload-performance telemetry
survives for analytics exactly as the original instrumentation brief asked. `upload_batches` is
untouched — a batch is never about a single video. No `content_slots`/`platform_posts` cleanup is
needed here since the check above already guarantees neither references the video before deletion
proceeds.

**Re-upload validated directly:** `test_delete_video_frees_the_file_hash_for_re_upload` (unit) and
`test_deleting_a_video_lets_the_exact_same_file_be_uploaded_again` (through the real API) both
delete a video, then re-upload the exact same bytes, and assert success with a brand-new video id
— `create_video_from_upload`'s duplicate-content check only ever sees rows that still exist.

**API:** `DELETE /api/videos/{video_id}` — 204 on success, 404 for "no such video" *or* "not
yours" (never distinguished, matching every other ownership check in this codebase), 409 with a
message safe to show directly (names no other account/video/slot/post) when the schedule/queue
check above refuses.

**Frontend:** `web/app/app/library/page.tsx` — a Delete button per row, an inline two-click
confirm ("Delete" → "Confirm delete" / "Cancel", no modal primitive), calling the new
`lib/api/videos.ts#deleteVideo()`. On success, re-fetches the list from the backend (same
"always reflect real server state" reasoning as the upload-finished refresh added in the prior
addendum) rather than optimistically removing the row locally. A 409 refusal renders as plain text
above the list and the video stays put. `lib/api/client.ts#apiRequest` now resolves to `undefined`
for a `204 No Content` response — previously every response was assumed to have a JSON body, and
calling `.json()` on an empty 204 body throws.

**Tests:** backend 803 passed (783 baseline + 20 net new — 8 in `test_media_storage.py`, 6 in
`test_api_videos.py`, 4 in `test_content_store.py`, 2 in `test_postgres_content_store.py`). The two
Postgres additions were not merely collected-and-skipped in this environment: `DATABASE_URL` is
loaded from this repo's own `.env` regardless of the shell's `env -u DATABASE_URL`
(`python-dotenv` reads the file directly), so they ran for real against `POSTGRES_TEST_SCHEMA` (a
disposable schema, truncated per test — never `POSTGRES_SCHEMA`/"public") and passed, incidentally
validating the Postgres-backend `delete_video`/`list_platform_posts_for_video` implementations
against a real database in the same run. Frontend 92 passed (92 baseline + 6 new — 1 in
`tests/lib/client.test.ts`, 2 in `tests/lib/videos.test.ts`, 3 in `tests/routes/library.test.tsx`),
lint clean, build clean. Zero existing tests weakened.

**Files changed:** `src/content_automation/persistence/{content_store,postgres_content_store,protocol}.py`,
`src/content_automation/media/media_storage.py`, `src/content_automation/api/routes/videos.py`,
`tests/test_media_storage.py`, `tests/test_api_videos.py`, `tests/test_content_store.py`,
`tests/test_postgres_content_store.py`, `web/lib/api/client.ts`, `web/lib/api/videos.ts`,
`web/app/app/library/page.tsx`, `web/tests/lib/client.test.ts`, `web/tests/lib/videos.test.ts`,
`web/tests/routes/library.test.tsx`.

**Remains intentionally deferred:** full queue/calendar editing (so a 409-blocked video can be
un-blocked from the UI), any bulk-delete action, any "are you sure" beyond the inline two-click
confirm, and an actual retention/auto-expiry policy (this is a manual, one-at-a-time user action
only).

**Live validation for the next real batch:** upload a real video, delete it from the Library UI,
confirm (a) the row disappears and a second listing call still shows it gone, (b) the object is
actually gone from the real Supabase bucket (not just that the app returned 204), and (c) the
exact same file can be selected and uploaded again successfully afterward. Separately, exercise
the refusal path once real scheduling/publishing exists for a video (assign it to a slot or create
a `platform_posts` row for it via the existing CLI/publish path) and confirm the Library shows the
409 message and the video is not removed.
