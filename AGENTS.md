# Content Automation / Pickle Batch

This file is the project-level entry point for any coding agent (Claude Code, Codex, or otherwise) working in this repository. Keep it thin: enough to orient a new agent and stop it from making a bad change, with pointers into the deeper deterministic documentation for anything task-specific. Do not turn this into a repository encyclopedia — detailed domain knowledge belongs in the docs this file points to, not copied in here.

## Project Purpose

Automates short-form video content scheduling and publishing: ingests recorded videos, transcribes and captions them locally, schedules them against a Google Calendar-backed content calendar, and publishes them to TikTok on schedule, unattended.

- **Internal/backend project name:** Content Automation — the name of this repository, its Python package (`content_automation`), its database, and its module/table naming throughout.
- **Public product name:** Pickle Batch — the external brand shown in `web/` (the Next.js frontend) only.

Do not rename internal modules, database tables, or backend concepts to "Pickle Batch" merely because that's the external brand — the two names are deliberately kept separate (see `CHANGELOG.md`'s 2026-09 brand-rename entry). Only rename something backend-side if a specific task explicitly calls for it.

## Current Product Stage

- **Milestone 2.1 (TikTok scheduled-publishing reliability): complete.** Due detection, atomic claiming, worker execution, crash recovery, retry/backoff, missed-schedule handling, token lifecycle/auto-refresh, and asynchronous publish reconciliation are all implemented and validated against the real TikTok Sandbox account — see `docs/evaluations/scheduling/`.
- **Milestone 3 (productization) is underway. Milestones 3.0–3.4 are complete** (backend package refactor; hosted architecture boundary; user authentication + ownership model; Postgres persistence migration; object storage + media lifecycle) — see `docs/evaluations/productization/milestone-3.0-backend-package-refactor.md` through `-3.4-object-storage-media-lifecycle.md`. The target hosted architecture is documented in `docs/architecture/hosted-product-boundary.md`; the ownership model in `docs/decisions/0007-user-ownership-model.md`; the Postgres persistence architecture in `docs/decisions/0008-postgres-persistence-migration.md`; the object-storage/media-lifecycle architecture (why `videos.storage_provider`/`storage_key` stay nullable on both backends, the `StorageProtocol` contract, retention policy) in `docs/decisions/0009-object-storage-media-lifecycle.md` — read all four before starting any Milestone 3.5+ work. Two real, independently-supported persistence backends exist (SQLite `ContentStore` default, Postgres `PostgresContentStore` real Supabase — `persistence.store_factory.build_content_store()` selects between them) and two real, independently-supported object-storage backends exist (`storage.local.LocalStorage` default, `storage.supabase_storage.SupabaseStorage` real Supabase — `storage.factory.build_storage()` selects between them). Neither factory is called by any CLI entry point by default — every `cli/*.py` still constructs `ContentStore()`/no storage directly, unchanged; Milestone 3.4 wired `storage` through `execute_claimed_platform_post`/`publish_video`/`worker.run_due_posts_once` (publishing) and `media.processing.process_one` (via optional `on_failed`/`on_assigned` hooks) plus a new `media.processing.process_storage_backed_video` entry point (hosted processing) as **optional**/additive capability, consulted only for a video that has actually been migrated to object storage (`storage_provider` set) — every other video, and every existing local-ingestion caller, is completely unaffected, exactly like SQLite's `user_id`-optional pattern from Milestone 3.2. Local CLI ingestion (`cli/process_content.py`) remains the fully-supported, unmodified local-development path — it does not require object storage to be configured at all.
- **Milestone 3.5 (mobile-first responsive web app shell) is complete** — see `docs/evaluations/productization/milestone-3.5-mobile-web-shell.md` and `docs/decisions/0010-frontend-app-shell.md`. `web/` has a product app shell (`/app`, `/app/library`, `/app/queue`, `/app/settings`) alongside the public marketing site (`/`, `/privacy`, `/terms`), a route-structural public/product boundary, and an API-client boundary (`web/lib/api/client.ts`).
- **Milestone 3.6 (real authentication + TikTok account connection) is COMPLETE** — see `docs/evaluations/productization/milestone-3.6-auth-tiktok-connection.md` (2026-09-26 addendum) and `docs/decisions/0011-real-authentication-and-tiktok-connection.md`. Real Supabase Auth (Google) replaces the Milestone 3.5 mock session — `NEXT_PUBLIC_ALLOW_MOCK_SESSION` no longer exists anywhere; a real FastAPI backend (`src/content_automation/api/` — `GET /api/me`, `/api/platforms/tiktok/{status,connect,callback,disconnect}`) verifies every request via `identity/token_verification.py`, never a client-supplied `user_id`; a hosted TikTok OAuth connection flow reuses `publishing/tiktok/auth.py`'s PKCE/token logic unmodified, with an encrypted `platform_credentials` table (Fernet, CAS-refreshed) alongside — not replacing — the local CLI's own token file/`fcntl` lock; `cli/link_bootstrap_user.py` has been run against real data (2026-09-21, verified directly against real Postgres: one real `auth_identities` row, all 5 pre-existing videos linked to the real user). A 2026-09-21 fix replaced the hosted TikTok connect flow's request-derived `redirect_uri` with the explicitly configured `config.TIKTOK_WEB_REDIRECT_URI`. A 2026-09-25 security review found and fixed two data-exposure gaps: the callback's `code`/`state` query parameters were reaching Railway's captured logs via uvicorn's default access logger (fixed with a scoped redacting log filter in `api/app.py`), and the Connected UI was surfacing TikTok's opaque `open_id` as if it were a real account label (fixed — `account_label` now stays `None` until a real display name is available via an approved scope). **2026-09-26: a real TikTok OAuth connect→callback→disconnect→reconnect cycle was completed and confirmed against the live deployed stack** — authenticated status/disconnect/post-callback status all returned `200`, the callback returned `302`, and Railway's captured logs for that callback confirmed `code`/`state` redacted — closing the one thing that was blocking COMPLETE status. See the evaluation doc's 2026-09-26 addendum for the full result and what was/wasn't independently verified by an agent versus reported directly by the user.
- **Milestone 3.6.1 (production API deployment) is live and validated.** Railway hosts the API (`railway.json` at repo root — Nixpacks build running `pip install -r requirements.txt && pip install .`, start command `python3 cli/run_api.py --host 0.0.0.0 --port $PORT`, healthcheck `/api/health`); the frontend is on Netlify's free subdomain (`picklebatch.netlify.app`, no custom domain owned yet). Confirmed live as of 2026-09-26: `GET /api/health` → `200`, CORS allows the real frontend origin, protected routes fail closed (`401`) without a token, and the real TikTok OAuth connect→callback cycle above completed successfully against this deployment — production environment variables (`TIKTOK_WEB_REDIRECT_URI`, `NEXT_PUBLIC_API_BASE_URL`, etc.) are confirmed working, not merely configured.
- **Milestone 3.7 (Batch Upload UX) readiness work is done; the milestone itself is IN PROGRESS** — see `docs/evaluations/productization/milestone-3.7-batch-upload-readiness.md`. `POST /api/videos` (real multipart batch upload — one `VideoUploadResult` per file, a bad file never aborts the rest of the batch) and `GET /api/videos` (the caller's own videos, newest first) now exist (`api/routes/videos.py`); `media.media_storage.create_video_from_upload()` is the new hosted-upload counterpart to the existing `upload_canonical_media()` (Milestone 3.4) — creates a brand-new owned `videos` row directly from freshly-received bytes (no prior local-ingestion step required), uploads it via the already-existing `StorageProtocol`, and is idempotent per (user, exact content); raises the new `DuplicateVideoContentError` (never reveals the other account) when the exact same content already belongs to a different user — `videos.file_hash` is globally UNIQUE at the schema level and was never rescoped per-user, a real pre-existing constraint this milestone had to handle, not fix. `web/app/app/library/page.tsx` is now a real client component (upload form + real listing), replacing the Milestone 3.5 static shell. Deliberately excludes transcription/scheduling/captions/publishing — this milestone's own scope guardrail. `requirements.txt` gained `python-multipart` (required by FastAPI to parse any file upload at all) — **must reach the next Railway deploy before a real upload will work in production.** **Still open, blocking COMPLETE status:** no real video file has been uploaded through the real deployed stack yet — see the evaluation doc's "Manual Validation Steps"/"Remaining Blockers" for exactly what that requires and what's already been verified without it (full backend/frontend test suites, real LocalStorage-backed integration tests). Do not treat 3.7 as closed on the assumption a real upload has happened — verify against `CHANGELOG.md`/the evaluation record first, exactly like 3.6's own pattern.
- **Next up: finish Milestone 3.7's live upload validation with real video files, then whatever Batch Upload UX polish that reveals is needed.** As of this writing: no hosted scheduler/worker, no hosted-worker-safe distributed lock for TikTok credential refresh (a real limitation, not an oversight — see ADR-0011 "Consequences"), no media deletion/retention policy implemented (deliberately deferred — see ADR-0009), no ffprobe/duration/dimensions enrichment of uploaded videos, no real scheduling controls, no real queue/calendar data wired to a backend. Do not introduce any of the still-missing pieces, and do not change existing scheduling/publishing/retry/reconciliation/auth behavior, unless the task you were given explicitly asks for it. Check `CHANGELOG.md`'s most recent entries and `PROJECT_STATE.md` before assuming what "current" means — both change often.

## Instruction Hierarchy

```text
global policies (~/.agents/)
    ↓
this file (AGENTS.md)
    ↓
task-relevant project docs (docs/decisions/, docs/evaluations/, PROJECT_STATE.md, README.md)
    ↓
implementation
```

Global policy always applies first and is never overridden by this file. This file adds only the Content-Automation-specific context needed to apply those policies correctly here — it does not restate them.

## Global Policies

Follow the shared, vendor-neutral policies before changing anything in this repository:

- `~/.agents/CODING.md` — modularity, existing-pattern conventions.
- `~/.agents/DOCUMENTATION.md` — code comments, changelog policy (`CHANGELOG.md` here), context boundaries.
- `~/.agents/GIT.md` — commit workflow and granularity.
- `~/.agents/SECURITY.md` — secrets/credential handling.
- `~/.agents/VERIFICATION.md` — required testing/verification before declaring work complete.

## Repository Structure

```text
src/content_automation/   runtime application package (import as content_automation.*)
  config.py                 shared configuration (REPO_ROOT, DB_PATH, TikTok/Calendar/Claude settings)
  media/                     video inspection, transcription, captions, classification, the ingestion pipeline; media_storage.py (Milestone 3.4, object-storage upload/materialize bridge)
  scheduling/                 due-post selection, worker, retry/backoff, reconciliation, crash recovery
  publishing/                   platform-neutral Publisher contract; publishing/tiktok/ (auth, publisher)
  persistence/                    ContentStore (SQLite, default) + PostgresContentStore (Milestone 3.3, real Supabase)
  storage/                        StorageProtocol; LocalStorage (default) + SupabaseStorage (Milestone 3.4, real Supabase)
  calendar/                        Google Calendar generation/management, posting cadence (cadence.py)

cli/                       thin CLI entry points — `python3 cli/<name>.py` — argument parsing only;
                            all logic lives in the package above and is directly importable by a future service
tools/evaluation/          engineering evaluation tooling (classifier/transcription benchmarking) —
                            not runtime code; distinct from the evaluation/ data directory below

tests/                     automated verification (pytest)
docs/architecture/         target hosted-architecture reference (hosted-product-boundary.md) — read before Milestone 3.2+ work
docs/decisions/            ADRs — genuine architecture decisions only
docs/evaluations/          recorded validation evidence, organized by domain (scheduling/, tiktok/, productization/)
evaluation/                golden dataset for tools/evaluation/evaluate_classifier.py (gitignored contents)
data/, content/            real SQLite DB + incoming/processed/failed video files — real business data
web/                       public Next.js frontend ("Pickle Batch") — separate Node toolchain, no shared code
                            with the Python backend in either direction; its own AGENTS.md is Next.js
                            tooling-generated (framework version notes), not a hand-authored project policy
```

An editable install (`pip install -e .`, via `pyproject.toml`) makes `content_automation` importable from anywhere in the venv. Never add a `sys.path` mutation as a substitute for this.

## Source Package Boundaries

Preserve these boundaries — place new runtime code in the package matching what it does, not which table it touches, and do not add new files at repository root:

- **`media/`** — anything about a video file itself or turning it into scheduling-ready metadata: inspection, transcription, captioning, classification, the ingestion pipeline.
- **`scheduling/`** — anything about *when* and *whether* a platform post executes: slot matching, due-post selection, atomic claiming, the worker, retry classification/backoff, reconciliation, crash recovery. Not to be confused with `calendar/cadence.py` (see next bullet) — different concern that happens to share the word "scheduling."
- **`publishing/`** — the platform-neutral `Publisher` contract and platform-specific implementations (`publishing/tiktok/`). A future second platform gets its own `publishing/<platform>/`, not a branch inside `tiktok/`.
- **`persistence/`** — `ContentStore` (SQLite) and `PostgresContentStore` (Milestone 3.3, Postgres) — the two implementations of `ContentStoreProtocol`, this repository's persistence boundary. No other module should touch a database directly (`import sqlite3`/`import psycopg`).
- **`storage/`** — `LocalStorage` and `SupabaseStorage` (Milestone 3.4) — the two implementations of `StorageProtocol`, this repository's object-storage boundary (put/exists/delete/materialize). No other module should call object-storage APIs directly; `media.media_storage` is the only module that resolves a video's owner and bridges `ContentStore` rows to a `StorageProtocol` instance.
- **`calendar/`** — Google Calendar integration and posting-cadence math (`cadence.py` — posting dates, pillar allocation; unrelated to `scheduling/`'s due-post concerns above despite the name).
- **`cli/`** — thin wrappers only (argument parsing, constructing the real publisher/store, printing the result). If you find yourself adding a conditional or a loop to a `cli/*.py` file, that logic almost certainly belongs in the package instead.
- **`tools/evaluation/`** — engineering benchmarking tooling, not part of the runtime application; safe to have heavier/optional dependencies (`requirements-eval.txt`) that the runtime package should never need.

## Coding / Refactor Rules

Follow `~/.agents/CODING.md`. In addition, specific to this repository's own established conventions (see `docs/evaluations/` for examples of the pattern in practice):

- Prefer the smallest existing abstraction that already solves the problem (e.g. reuse `ContentStore.update_platform_post_if_unchanged`'s optimistic-concurrency pattern rather than inventing a new one) over adding a new one.
- A one-pass, no-daemon, no-busy-loop shape is the established convention for anything that "runs periodically" (`worker.py`, `reconciliation.py`, `crash_recovery.py`) — a future scheduler is expected to invoke these repeatedly, not for them to loop internally.
- Timestamp conventions are deliberate and inconsistent on purpose — `scheduled_at` is naive local time (matches Google Calendar's stored convention); `updated_at`/`published_at`/`next_status_check_at` are aware UTC. Check which convention a field already uses before adding logic that compares it against `now`; do not silently normalize one into the other.

## Testing Requirements

Follow `~/.agents/VERIFICATION.md`. Specific to this repository:

- `python3 -m pytest` — **must run inside `.venv` (`.venv/bin/python3 -m pytest`), not a bare `python3`**, or `content_automation` fails to import and every test errors at collection. Current baseline: **758 passing**. Check `CHANGELOG.md`'s most recent entries for the current count before trusting this number; it moves often. 5 of these are from the Milestone 3.6 explicit-web-redirect-uri correction (`test_api_platforms_tiktok.py` — configured redirect_uri used consistently, request host/scheme ignored, fails closed when unset or non-https). 20 more are from Milestone 3.3 (18 Postgres-dependent, skipped without `DATABASE_URL`; 2 unconditional — `tests/test_postgres_content_store.py`/`test_store_factory.py`), always against `config.POSTGRES_TEST_SCHEMA`, never `config.POSTGRES_SCHEMA` ("public", the real application schema). 50 more are from Milestone 3.4 (`tests/test_storage_local.py`, `test_storage_factory.py`, `test_media_storage.py`, `test_publish_tiktok_storage.py`, `test_migrate_media_to_object_storage.py`, `test_process_storage_backed_video.py` — 41 total, unconditional, no network); `test_storage_supabase.py` (7) and `test_media_storage_postgres.py` (2) are Postgres/Supabase-dependent and skipped automatically without `DATABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` (i.e. `SERVICE_ROLE_KEY`), always against `config.SUPABASE_STORAGE_TEST_BUCKET`, never `config.SUPABASE_STORAGE_BUCKET` (the real media bucket). 57 more are from Milestone 3.6 (`test_token_verification.py`, `test_user_resolution.py`, `test_link_bootstrap_user.py`, `test_credential_store.py`, `test_api_me.py`, `test_api_platforms_tiktok.py` — all 57 unconditional, no real Supabase/TikTok credentials required; real cryptography (self-signed RSA keys) and mocked TikTok HTTP calls, never live ones).
- No live TikTok API calls, no real Google Calendar writes, and no real-DB (`data/content.db`) mutation from automated tests — every test that needs a `ContentStore` uses `tmp_path`; every test that needs a TikTok/Calendar client mocks it. If a task requires touching the real TikTok account or real Calendar, treat that as a hard-to-reverse external action requiring explicit user confirmation first (see `~/.agents/SECURITY.md` and this repository's own pattern in `docs/evaluations/scheduling/milestone-2.1.9-real-unattended-tiktok-validation.md` for how that confirmation was sought and scoped).
- Never delete or weaken an existing test to make a refactor pass. If a test count changes, the reason must be explainable (see Milestone 3.0's evaluation doc for what that explanation should look like).
- `web/` (frontend, Milestone 3.5+): `npm run test` (Vitest + React Testing Library) — current baseline: **67 passing**, `npm run lint` (ESLint) and `npm run build` (production static export, which also runs the TypeScript typecheck) must both be clean. Run these from inside `web/`, independently of the Python suite above — the two have no shared tooling or code. `npm run build` requires `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` to be set (even to placeholder-shaped values for a local validation build) — a production build with them unset fails closed by design (Milestone 3.6; see ADR-0011). `tests/lib/no-secrets-in-client-bundle.test.ts` and `tests/lib/session.test.tsx` are guardrail tests, not ordinary coverage — do not delete or weaken them; see ADR-0010's "Authentication Boundary" and ADR-0011's "Mock session removed" for what they enforce.

## Documentation / Evaluation Requirements

Follow `~/.agents/DOCUMENTATION.md` for changelog/comment policy — not restated here. Specific to this repository:

- `docs/decisions/` — ADRs for genuine architecture decisions only. Most milestone work is *not* an ADR; do not create one unless the work genuinely changes an architectural direction.
- `docs/evaluations/<domain>/milestone-X.Y-<name>.md` — the canonical record of validation evidence for a completed milestone (what was built, how it was tested, real-data guardrail results). Read the relevant prior one before starting related work; write a new one when completing milestone-shaped work, following the existing ones' structure.
- `PROJECT_STATE.md` — current architecture/behavior narrative. `README.md` — setup, run commands, project structure. Both are live documents; update them when a change makes them wrong, not just when a milestone doc is written.
- Do not rewrite historical `docs/evaluations/` records to match current file paths after a refactor — they are evidence of what was true when they were written. Add a short forward-pointing note instead (see Milestone 3.0's own doc, and the note it added to `PROJECT_STATE.md`'s "Directory Ownership" section, for the pattern).

## CLI / Runtime Rules

- Supported invocation: `python3 cli/<name>.py [args]`, from the repository root, inside the project's `.venv`. Do not invoke a package module's file path directly as a script.
- `cli/*.py` files exist so a future hosted service can import the same package functions directly instead of shelling out to these scripts — keep that true by never putting logic only the CLI can reach.

## Data and Credential Safety

Follow `~/.agents/SECURITY.md`. Specific to this repository's credentials, none of which are ever committed:

- `data/content.db` — the real production SQLite database. Real business data. Read real rows read-only when investigating; never mutate them outside an explicitly-confirmed, explicitly-scoped task.
- `~/.config/content-calendar/` — TikTok OAuth token (`tiktok_token.json`), Google Calendar OAuth token/client secrets. Never print token values to terminal output or into a file.
- `.env` — `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `DATABASE_URL` (Milestone 3.3 — real Postgres connection string with credentials), `SERVICE_ROLE_KEY` (Milestone 3.4 — Supabase service-role key, full storage access, server-side only; note the variable name has no `SUPABASE_` prefix despite `SUPABASE_URL` having one), `SUPABASE_JWT_SECRET` (Milestone 3.6 — only used in `hs256` verification mode, server-side only), `CREDENTIAL_ENCRYPTION_KEY` (Milestone 3.6 — symmetric key encrypting hosted TikTok credentials at rest; rotating it invalidates every stored hosted TikTok connection). Never commit, never echo into logs or docs. `DATABASE_URL`/`SERVICE_ROLE_KEY`/`SUPABASE_JWT_SECRET`/`CREDENTIAL_ENCRYPTION_KEY` specifically: never print them, even partially, in terminal output or a file — if you need to confirm which host/project a value points to, parse and print only that portion.
- `web/.env`/`.env.local` — `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` (Milestone 3.6 — meant to be public, the anon key is a public API key by Supabase's own design; never confuse with the server-only `SERVICE_ROLE_KEY` above, which has no reason to exist anywhere in `web/`).
- `~/growth_agency/credentials/service-account.json` — shared Google service account, used only for the explicit `--calendar <id>` override path, not the normal OAuth path.

## Scope Guardrails

Do not introduce, unless a task explicitly asks for it:

- A hosted service layer: FastAPI or any other web framework, Postgres/Supabase, user authentication/ownership, object storage, a hosted/cron scheduler.
- New publishing platforms, new TikTok API behavior, or changes to already-validated scheduling/retry/reconciliation semantics (Milestone 2.1's own scope guardrails still apply post-3.0 — see the relevant `docs/evaluations/scheduling/` records before changing any of that behavior).
- Database schema redesign or timestamp-convention normalization (see "Coding / Refactor Rules" above for why the current inconsistency is deliberate).
- Changes to `web/` as a side effect of backend work — it has no shared code with the Python backend in either direction.
- Batch upload, real scheduling controls, real queue/calendar backend data, or hosted workers inside `web/` — real authentication and the first real TikTok connection now exist (Milestone 3.6), but those specific product surfaces remain future scope; see ADR-0011.

## Where to Find More Detailed Instructions

- `README.md` — setup, run commands, current project structure.
- `PROJECT_STATE.md` — current architecture and behavior, by area (routing strategy, captions, TikTok publishing, credentials, testing, etc.).
- `docs/architecture/hosted-product-boundary.md` — the target hosted architecture (API vs. background-job boundary, persistence/media/credential boundaries, migration risks) that Milestones 3.2+ should build toward.
- `docs/decisions/0008-postgres-persistence-migration.md` — why Postgres was added as a second persistence backend the way it was (two parallel concrete classes, driver, migration framework, timestamp representation, RLS deferral).
- `docs/decisions/0010-frontend-app-shell.md` — why the `web/` app shell is structured the way it is (route groups, API-client and session boundaries, the temporary mock-session Netlify flag and the blocker before real user data access).
- `docs/decisions/0011-real-authentication-and-tiktok-connection.md` — why real authentication is shaped the way it is (Supabase Auth/Google, JWKS token verification, the `users`/`auth_identities` mapping, the bootstrap-user linking mechanism) and how the hosted TikTok OAuth connection flow works (state binding, credential storage/encryption, the CAS-based hosted refresh and its one honestly-documented residual limitation).
- `docs/decisions/` — why the architecture is shaped the way it is (ADRs).
- `docs/evaluations/<domain>/` — what was built and how it was validated, per milestone.
- `CHANGELOG.md` — the most current, dated record of what actually changed and why; check it first when "current state" matters and this file might be stale.
