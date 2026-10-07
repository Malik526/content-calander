# Milestone 4.1 — Instagram OAuth Integration: Evaluation

Brief: `docs/roadmap/milestone-4.1/M4.1A-instagram-oauth-integration.md` (GREEN).
Architecture: [ADR-0018](../../decisions/0018-instagram-integration-architecture.md) Decision 6
and its 2026-10-06 addendum.

## Status

**Implemented and verified with mocked Meta responses only (2026-10-06). Live Meta OAuth is not
verified.** No Meta app credentials or tester account were used, no request reached Meta, nothing
was deployed, and migration `0011` has not been applied to any real database. Live validation is
M4.1B, blocked on Meta developer-app and tester access. Instagram publishing is still Milestone
4.2 (`publishing_available=False`).

## What was built

| Area | Where |
|---|---|
| Authorization URL, code → short-lived → long-lived exchange, refresh, `GET /me` | `publishing/instagram/oauth.py` |
| Encrypted long-lived credential, refresh timing, lock + CAS | `publishing/instagram/credential_store.py` |
| `@username` identity (live, best-effort, never persisted) | `publishing/instagram/identity.py` |
| Shared Fernet helpers (TikTok re-exports) | `publishing/credential_encryption.py`, `publishing/tiktok/credential_store.py` |
| Connect / public callback / disconnect / status with identity | `api/routes/platforms_instagram.py` |
| Server-owned exact-match return-target allowlist | `api/oauth_return_targets.py` |
| `oauth_states.return_target`, platform-scoped atomic consumption, external-account update | `persistence/{protocol,content_store,postgres_content_store}.py`, Postgres migration `0011` |
| Access-log redaction of `code`/`state` on both callback paths | `api/app.py` |
| Registry: Instagram `connection_available=True` | `publishing/platforms.py` |
| Settings Connect/Disconnect/`@username`/callback outcomes, own state and cache updates | `web/components/app/InstagramConnectionSection.tsx`, `web/hooks/useInstagramActions.ts`, `web/lib/instagramCallback.ts`, `web/lib/api/platforms.ts` |

New configuration (both optional, no secrets): `OAUTH_EXTRA_RETURN_TARGETS` (empty) and
`INSTAGRAM_TOKEN_REFRESH_WINDOW_SECONDS` (30 days). Connecting also needs `FRONTEND_BASE_URL`
(or its CORS-origin default) to resolve to an https URL, or `http://localhost`. An empty
`FRONTEND_BASE_URL` (the blank line `.env.example` ships) falls back to that default; see the
manual takeover review below.

## Automated evidence

All Meta HTTP traffic is mocked at `requests.request`. Nothing needs network or credentials.

| Claim | Tests |
|---|---|
| Authorization URL has client id, exact redirect URI, both scopes, `response_type=code`, fresh state, no PKCE, no secret | `test_instagram_oauth.py`, `test_api_platforms_instagram_oauth.py` |
| Connect stores an owner-bound Instagram state with `code_verifier=''`, the exact redirect URI, TTL and allowlisted return target | `test_api_platforms_instagram_oauth.py` |
| Arbitrary, protocol-relative, lookalike, user-info, prefix, non-https, `javascript:` and custom-scheme targets are rejected (400, nothing stored) | `test_api_platforms_instagram_oauth.py`, `test_oauth_return_targets.py` |
| Incomplete configuration → `connect_available=false` and connect 503 | `test_api_platforms_instagram_oauth.py`, `test_api_platforms_instagram.py` |
| Success: state owner (never callback input) gets an ACTIVE connection with the account id, only the encrypted long-lived token is stored, redirect keeps the target's own query | `test_api_platforms_instagram_oauth.py` |
| Denied, missing code, missing, unknown, replayed, expired and wrong-platform state → safe outcome, no credential, no exchange; another platform's state is never consumed (both directions) | `test_api_platforms_instagram_oauth.py`, `test_oauth_state_persistence.py` |
| Malformed short/long responses, rejected exchanges, non-JSON → `exchange_failed`, no credential, nothing provider-written in the redirect | `test_api_platforms_instagram_oauth.py`, `test_instagram_oauth.py` |
| Errors never contain tokens, the code, the app secret or response bodies, including transport errors whose message carries the URL | `test_instagram_oauth.py` |
| Reconnecting a different account updates `external_account_id` and `@username`; the numeric id is never returned | `test_api_platforms_instagram_oauth.py` |
| Identity failure (5xx, 4xx, non-JSON, no username) keeps the connection with no invented label, nothing secret logged | `test_api_platforms_instagram_oauth.py` |
| Refresh only when ≥ 24 h old and inside the window, under the per-connection lock, CAS write; reuse after another holder refreshed; lost CAS re-reads; 4xx → reauthorization; transient failure keeps a valid token; lock timeout → `CREDENTIAL_REFRESH_BUSY`; 8 concurrent threads → exactly one refresh | `test_instagram_credential_store.py` |
| Disconnect deletes only the caller's Instagram credential, is idempotent, leaves other users and TikTok alone | `test_api_platforms_instagram_oauth.py` |
| `return_target` on fresh and pre-4.1 SQLite databases; null legacy rows; concurrent consumers → one winner | `test_oauth_state_persistence.py` |
| Same on Postgres (migration `0011`) | `test_postgres_content_store.py`, skipped without `DATABASE_URL`; run and passed during the manual takeover against the disposable test schema |
| `code`/`state` redacted on both callback paths; other params and routes unchanged | `test_api_access_log_redaction.py` |
| Shared encryption: TikTok re-exports are the same objects; key errors fail closed | `test_credential_encryption.py`; existing TikTok tests unchanged except their key patch now targets the shared module |
| No Instagram `platform_posts` after connecting | `test_api_platforms_instagram_oauth.py`, `test_platforms.py` |
| Settings: Connect only when available, progress, `@username`, disconnect writes the user-scoped cache, callback messages, only `instagram` param removed, status refetched, TikTok independent | `web/tests/routes/settings-instagram.test.tsx`, `web/tests/lib/instagramCallback.test.ts`, `web/tests/lib/platforms.test.ts` |

Results (2026-10-06, local, `DATABASE_URL` unset to match the controller; final numbers after
the manual takeover below):

- `.venv/bin/python -m pytest -q`: **1312 passed, 92 skipped** (was 1142 / 88; the 4 new skips
  are the Postgres tests above; the implementer's run reported 1310, and the takeover added 2
  configuration tests).
- `web`: `npm ls --depth=0` OK, `npm run lint` clean, `npx tsc --noEmit` clean,
  `npm test -- --configLoader runner` **24 files / 205 tests passed** (was 22 / 181).
- Changed tests, with reasons: Milestone 4.0's `connect_available` stays-false test and the
  registry test now assert 4.1 behavior. Four TikTok test files patch the encryption key on the
  shared module. `app-routes.test.tsx` waits for both platform cards instead of the first match,
  since the Instagram card now settles in its own component.
- `npm run build`: the implementer's run could not build because Turbopack refuses a symlinked
  `node_modules` (an environment issue, not application code). The takeover built with a real
  `npm ci` install and placeholder `NEXT_PUBLIC_SUPABASE_*` values: **passed**, all routes
  prerendered.

## Manual takeover review (2026-10-06)

The Autobuild run `2026-10-06-M4.1A` implemented this brief, then stopped `FAILED`
(`safety_violation`) before validation, review or checkpoint. Its filename guard treats any
`.env.*` change as secret-like, and the implementer had (correctly) documented the new
configuration in the tracked `.env.example`. Every credential-like value in that file is empty, so
this was a false positive. The work was finished by hand from the preserved worktree on its
`agent/M4.1A-build-instagram-oauth-integration` branch.

- Reviewed the whole diff against the brief and ADR-0018 Decision 6: state creation and atomic,
  platform-scoped consumption; owner binding through state only; exact-match return targets
  re-checked at callback; short- → long-lived exchange; refresh window, lock and CAS; encrypted
  storage; `@username` identity; disconnect; log redaction; migration `0011` packaging (matched by
  the existing `postgres_migrations/*.sql` package data and the numbered loader); the TikTok
  encryption refactor; Settings cache behavior. No security defect found.
- **Fixed:** `config.FRONTEND_BASE_URL` used `os.getenv(name, default)`, so the blank
  `FRONTEND_BASE_URL=` line in `.env.example` disabled Instagram Connect and sent TikTok's
  callback to a host-relative `/app/settings`. An empty value now counts as unset and falls back
  to the first CORS origin, like the other optional settings
  (`test_instagram_configuration.py`, 2 new tests). The `.env.example` note that told people to
  delete the line instead was corrected.
- Kept on purpose: the not-configured Instagram card still says "Connecting Instagram is coming
  soon." Production has no Meta app yet (M4.1B), so that is accurate, and no button is offered.
- Postgres: with `DATABASE_URL` set (disposable `pickle_batch_test` schema only, never `public`)
  the full suite gives **1393 passed, 11 skipped**. The 11 skips are Supabase Storage tests needing
  a service-role key, unrelated to this milestone. That includes migration `0011` and
  platform-scoped consumption on real Postgres, plus the hosted worker/recovery suites.

## Not verified (M4.1B, needs Meta access)

- A real consent → callback → token exchange against Meta with a tester account, including
  Meta's real response shapes (wrapped vs. flat, numeric vs. string ids).
- That the long-lived refresh works on a real token ≥ 24 h old.
- Which id `/<IG_ID>/media` needs (exchange `user_id` vs. `/me` `user_id`), before 4.2.
- Railway logs for a real Instagram callback (redaction is unit-tested only).
- Migration `0011` on the real application schema (`public`); it ran only in the disposable test
  schema.
