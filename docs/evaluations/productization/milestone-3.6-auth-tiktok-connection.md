# Milestone 3.6 — Real Authentication + TikTok Account Connection

## Objective

Replace the Milestone 3.5 mock-session boundary with real, server-verifiable user authentication, then
build the first real user-owned product integration: connecting a TikTok account from the Pickle Batch
web app.

## Phase 1 — Guidance Read First

`AGENTS.md`, `docs/architecture/hosted-product-boundary.md`, ADR-0007/0008/0009/0010, and the Milestone
3.5 evaluation record were re-read before any code change, confirming the exact blocker this milestone
closes: ADR-0010's "BLOCKER BEFORE REAL USER DATA ACCESS" (real auth; remove the mock path; remove the
Netlify flag; verify fail-closed; never trust a client-supplied `user_id`).

## Phase 2 — Auth Provider Evaluation

Evaluated Supabase Auth, Auth.js/NextAuth, Firebase Auth/Clerk/Auth0, and a custom Google-Identity-
Services + self-issued-JWT flow against: web support, future Expo/React Native support, Google login,
backend-verifiable tokens, durable provider subject, mapping into `auth_identities`, cost, deployment
simplicity. **Selected: Supabase Auth, Google as the first provider** — see ADR-0011 "Authentication
provider" for the full comparison and why each alternative was ruled out. Confirmed with the user before
building anything: they chose Supabase Auth + Google, confirmed the existing bootstrap user
(`local@pickle-batch.local`) should be linked (not orphaned) on first real login, and confirmed
Supabase Auth/a Google OAuth client both still needed first-time setup (given a step-by-step checklist;
setup was in progress as of this writing — see "Deferred").

## Phase 3 — Backend API Surface

`src/content_automation/api/` (new): `GET /api/me`, `GET /api/platforms/tiktok/status`,
`POST /api/platforms/tiktok/connect`, `GET /api/platforms/tiktok/callback`,
`POST /api/platforms/tiktok/disconnect`, plus an unauthenticated `/api/health`. No generic CRUD. Run
via `cli/run_api.py` (thin wrapper, matching the existing `cli/*.py` convention).

## Phase 4 — Auth Verification Boundary

`identity/token_verification.py`'s `verify_access_token()` derives `current_user_id` exclusively from a
verified bearer token (`Authorization: Bearer <token>`) — never from anything else. Two verification
modes (`config.SUPABASE_AUTH_JWT_MODE`): `jwks` (default — Supabase's asymmetric signing keys, no shared
secret server-side, `PyJWKClient`-cached) and `hs256` (legacy shared secret, `config.SUPABASE_JWT_SECRET`,
server-side only). `api/dependencies/auth.py`'s `get_current_user` is the one dependency every protected
route uses.

## Phase 5 — User / Auth Identity Mapping

`identity/user_resolution.py`'s `resolve_or_create_user()`: look up `auth_identities` by
`(provider, provider_subject)`; create a `users` row + link on first login otherwise. Preserves the
application-owned integer `users.id` — the external provider's `sub` never becomes the primary id.
Deliberately does not merge accounts by matching email (`AccountConflictError` instead) — see ADR-0011
"User / Auth Identity Mapping" for the security reasoning.

## Phase 6 — Existing Local Bootstrap User

`cli/link_bootstrap_user.py` (new): an explicit, one-off, human-run script that links a real verified
identity to the *existing* bootstrap user (never automatic). Refuses to proceed if the bootstrap user
doesn't exist, if the identity is already linked elsewhere, or if the bootstrap user already has a
different identity for the same provider. Dry-run supported. **Not yet run against real data** — running
it requires a real `provider_subject`, obtained only after the user completes real Google/Supabase setup
and logs in once; that setup was still in progress as of this writing (see "Deferred").

## Phase 7 — Mock Session Removed

`lib/session.tsx` no longer defaults to `DEV_MOCK_USER` unconditionally — it wraps real Supabase Auth.
`NEXT_PUBLIC_ALLOW_MOCK_SESSION` was deleted from `lib/session.tsx`, `netlify.toml`, and
`web/.env.example` — grepped to confirm zero remaining references anywhere in the repository. A
production build with Supabase unconfigured now fails closed unconditionally (verified directly, see
Phase 29). The only remaining fallback (`DEV_MOCK_USER`) requires *both* Supabase being unconfigured
*and* `NODE_ENV !== "production"` — a local-dev-only convenience, resolved synchronously at render time
(not deferred into an effect).

## Phase 8 — Login UI

`web/app/login/page.tsx` (new, public route): a single "Continue with Google" action calling
`supabase.auth.signInWithOAuth({ provider: "google", ... })`, with loading and retryable-error states.
No password auth, no password reset, no multi-provider linking UI — matching the brief's own guardrail.

## Phase 9 — Session Persistence

Supabase's own `persistSession: true` + `autoRefreshToken: true` client config (`lib/supabase/client.ts`)
handles session survival across a page refresh — not React in-memory state alone.
`web/app/auth/callback/page.tsx` (new) is where Google/Supabase redirects back to; it waits for
`supabase.auth.getSession()`/`onAuthStateChange` to resolve a real session, then navigates into `/app`.
`/app/*` is additionally gated client-side (`components/app/AppAuthGate.tsx`, redirects to `/login` when
unauthenticated) — a UX affordance, not the security boundary (the backend's independent token
verification is).

## Phase 10 — Future Native Compatibility

Supabase Auth's SDK family works identically from React Native/Expo; `verify_access_token()` has no
cookie/browser-specific dependency (a bearer token is a bearer token); `lib/api/platforms.ts`'s typed
functions take the access token as an explicit parameter rather than reading it from a web-only source.
See ADR-0011 "Future native compatibility."

## Phase 11 — Existing TikTok OAuth Revisited

Read `publishing/tiktok/auth.py` in full before writing any hosted code. Confirmed reusable as-is:
`generate_state`, `generate_pkce_pair`, `build_authorization_url`, `exchange_code_for_token`,
`refresh_access_token` all take `redirect_uri`/tokens as parameters rather than hardcoding a local path —
no rewrite needed. Confirmed NOT reusable as-is: the local callback (a hand-rolled `http.server` instance
opened via `webbrowser.open()` on the same host as the CLI process) and the credential store (one global
local file + a process-local `fcntl` lock) — both are genuinely local-CLI-shaped and needed hosted
equivalents, not modification.

## Phase 12 — Hosted OAuth Flow

`api/routes/platforms_tiktok.py`'s `connect`/`callback`: `connect` (protected) generates a fresh
state/PKCE pair, persists a server-side `oauth_states` row bound to the caller's verified `user.id`
(redirect URI resolved from the request itself via `request.url_for`), and returns the real TikTok
authorization URL. `callback` (deliberately unauthenticated — TikTok redirects the browser directly, no
bearer header available) validates and atomically consumes the state (`consume_oauth_state` — a CAS
`UPDATE ... WHERE consumed_at IS NULL`), exchanges the code, and persists the credential under the
state's own bound `user_id` — never a value the request itself could supply. See ADR-0011 "TikTok
OAuth" for the full flow diagram and security reasoning.

## Phase 13 — Multi-User TikTok Credential Storage

New `platform_credentials` table (`UNIQUE(platform_connection_id)`, SQLite + Postgres migration
`0003_add_platform_credentials_and_oauth_states.sql`), deliberately separate from `platform_connections`
(which ADR-0007 already documented to carry no secrets). One TikTok connection per user for V1, matching
the pre-existing `platform_connections` constraint. Never stored in browser localStorage; never returned
in any API response (enforced by narrow, explicit Pydantic response models, not `__dict__`).

## Phase 14 — Credential Encryption

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography` — already a transitive dependency, not new) under
`config.CREDENTIAL_ENCRYPTION_KEY` (server-side only, no default, never logged). Evaluated against a
managed secrets service/KMS and judged premature for this milestone's actual requirement — see ADR-0011
"Credential storage" for the reasoning. Verified directly: `tests/test_credential_store.py` proves a
saved credential's `encrypted_payload` never contains the plaintext access/refresh token string.

## Phase 15 — Replaced the Host-Local Token Lock (for the hosted path)

`get_hosted_tiktok_access_token()` (`publishing/tiktok/credential_store.py`) replaces `fcntl.flock` with
a bounded optimistic-concurrency (CAS) retry loop against `platform_credentials.updated_at` — the same
`update_platform_post_if_unchanged` pattern already proven elsewhere in this codebase. This closes the
*data-integrity* risk fully (two concurrent refreshes can never both persist). One honestly-documented
residual limitation remains and is **not** implemented this milestone: two truly simultaneous refresh
attempts could both call TikTok's own refresh endpoint before either persists, and TikTok's rotation
behavior might reject the second — a real distributed lock would close this fully. Recorded explicitly
per the brief's own allowance, not silently ignored. Local CLI ingestion's own `fcntl` lock is completely
untouched.

## Phase 16 — Platform Connection Status API

`GET /api/platforms/tiktok/status` returns `{platform, connected, status, account_label}` — never
`access_token`/`refresh_token`/`client_secret`/any raw credential. Verified directly:
`tests/test_api_platforms_tiktok.py::test_callback_never_returns_the_access_or_refresh_token`.

## Phase 17 — Settings UI

`web/app/app/settings/page.tsx` rewritten: the Milestone 3.5 mocked platform-connection block is gone,
replaced with real `lib/api/platforms.ts` calls. States implemented: loading, disconnected, connecting
(client-side, while the connect request is in flight before the browser navigates to TikTok), connected,
error (both load-failure and action-failure, each with a retry/clear message). **Not implemented:** a
distinct "reauthorization required" UI state — the backend's status response does not currently
distinguish that from "disconnected" (see "Deferred").

## Phase 18 — Connect Button

Real action: `POST /api/platforms/tiktok/connect` → `window.location.href = authorization_url`. Found
and fixed a real UI-primitives gap while building this: the existing `Button` component only supported
`href`-based navigation, so a real *action* (not a link) would have needed an `href="#"` +
`preventDefault()` anti-pattern (bad accessibility, a genuine navigation hazard). Extended `Button`
(`components/ui/Button.tsx`) with a real `type="button"` + `onClick` mode instead — a real `<button>`
element for real actions, never a disguised link. All 9 pre-existing `href`-based `Button` usages verified
unaffected (discriminated union type, `href` and `onClick` mutually exclusive).

## Phase 19 — OAuth Callback UX

Every callback outcome (success or failure) redirects to `/app/settings?tiktok=<reason>` —
`connected`/`denied`/`invalid_state`/`expired_state`/`exchange_failed`, each mapped to a distinct, plain-
language message client-side; no raw TikTok error detail or exception text ever reaches the query string
or the browser. Verified: `test_callback_denied_by_tiktok_redirects_with_denied_reason` asserts the raw
TikTok `error` value (`access_denied`) is *not* present in the redirect URL.

## Phase 20 — Disconnect

`POST /api/platforms/tiktok/disconnect` deletes the `platform_credentials` row and sets
`platform_connections.status = DISCONNECTED` — never deletes the connection row itself (preserves
identity/history), never touches `videos`/`platform_posts`. No provider-side token revocation call was
implemented — TikTok's current OAuth documentation does not publish a distinct revoke endpoint this
codebase could target confidently; recorded as a gap, not silently assumed unnecessary.

## Phase 21 — Ownership / Tenant Isolation Tests

Proven directly with two real users (A, B) across the full connect → callback → status → disconnect
cycle: A's connection is completely independent of B's; disconnecting A never affects B; a state bound
to A's connect attempt resolves to A's connection regardless of which "current session" happens to hit
the callback (`test_callback_binds_to_the_user_who_initiated_connect_regardless_of_who_hits_the_callback`).
Real Postgres was not exercised for these specific new tables this milestone (no live Postgres connection
available in this environment) — see "Deferred"; the SQLite path (the same `ContentStoreProtocol`
methods, same SQL semantics ported 1:1 in the Postgres migration) is what these tests actually run
against, matching this codebase's existing "SQLite is the fast/default test backend" convention.

## Phase 22 — Auth Security Tests

`tests/test_api_me.py`: no token → 401; invalid signature → 401; malformed header → 401; valid token →
correct user, created on first request and resolved identically on a second. `tests/test_token_verification.py`
(10 tests): real RSA-keypair-signed JWTs, expired/forged-signature/wrong-audience/missing-claim rejection,
both `jwks` and `hs256` modes, fail-closed misconfiguration handling.

## Phase 23 — OAuth Security Tests

PKCE/state generation: pre-existing, unmodified, already tested (`tests/test_tiktok_auth.py`). New this
milestone: state replay rejection (`test_callback_state_replay_is_rejected`), expired state rejection
(`test_callback_expired_state_is_rejected`), unknown/malformed state rejection
(`test_callback_with_unknown_state_is_rejected`), TikTok-denial handling with no leaked detail, and
token-exchange-failure handling (`exchange_failed` reason, no raw exception surfaced).

## Phase 24 — Credential Leakage Review

`git grep`/`grep` across `web/` and the new backend modules for
`access_token`/`refresh_token`/TikTok client secret/Supabase JWT secret/service-role key/`DATABASE_URL`:
every match outside test fixtures and explicit "never do this" comments is either a Pydantic response
model's deliberate *exclusion* of the field, or `tests/lib/no-secrets-in-client-bundle.test.ts`'s own
guardrail definition. No credential value was printed during any verification step in this record.

## Phase 25 — Frontend API Client Integration

`web/lib/api/platforms.ts` (new): `getMe`, `getTikTokConnection`, `connectTikTok`, `disconnectTikTok` —
all typed, all routed through the existing `lib/api/client.ts#apiRequest()` (extended with an
`accessToken` option that attaches `Authorization: Bearer <token>` — the caller supplies it, `client.ts`
itself stays session/framework-agnostic). No component calls `fetch()` directly. Types
(`lib/api/types.ts`'s new `CurrentUser`/`TikTokConnectionStatus`) mirror the backend's actual Pydantic
response shapes, not database rows.

## Phase 26 — CORS / Deployment Boundary

`config.API_CORS_ALLOWED_ORIGINS` (comma-separated, default `http://localhost:3000`) — an explicit
allowlist, never `"*"`, applied via `CORSMiddleware` in `api/app.py`.

## Phase 27 — Backend Runtime Selection

`api/dependencies/auth.py`'s `get_store` calls `persistence.store_factory.build_content_store()` — the
same `DATABASE_URL`-driven, no-silent-fallback selector every other part of this codebase already uses —
never a hardcoded `ContentStore()`/`LocalStorage()`. A production deployment with `DATABASE_URL` set gets
`PostgresContentStore` automatically; this environment's tests (no `DATABASE_URL` set) exercise the
SQLite path, matching the existing convention.

## Phase 28 — Existing Data Safety

No real data was touched this milestone. `cli/link_bootstrap_user.py` was written and tested against a
temp SQLite database only (`tests/test_link_bootstrap_user.py`, including a direct byte-for-byte
before/after comparison of an existing `videos` row proving the script touches nothing but
`auth_identities`) — running it against the real database is a separate, explicitly-confirmed action not
taken as part of this record, since it requires a real `provider_subject` the user doesn't have yet (see
"Deferred").

## Phase 29 — End-to-End Authenticated Flow

Proven with real (self-signed) cryptography end to end, not mocked away: `tests/test_api_me.py` signs a
real JWT with a real RSA private key, sends it as a real bearer header through a real FastAPI
`TestClient` request, and confirms the backend actually creates a real `users`/`auth_identities` row
(verified via a second, independent database connection) and returns the correct response. The full
hosted TikTok connect → callback → status → disconnect cycle was proven the same way, with only TikTok's
own token-exchange HTTP call mocked (no real TikTok credentials exist yet to test against — see
"Deferred"). **No real TikTok OAuth connection and no video publish were performed** — matching the
brief's own explicit "do not publish a video as part of 3.6" guardrail, and reflecting that real Google
Cloud/Supabase setup was still in progress as of this writing (see "Deferred").

## Phase 30 — Frontend UX Validation

Manual + automated responsive validation performed for the new `/login` route (320–1440px, real headless
Chromium against the actual static export, same methodology as Milestone 3.5) — clean at every width, no
overflow. `/app` redirect-when-unauthenticated behavior confirmed in a real browser: visiting `/app/`
with no session lands on `/login/`. The connected/error/loading Settings states were validated via the
frontend test suite (jsdom) rather than a live browser pass, since they depend on backend responses this
environment has no live Supabase/TikTok credentials to produce end-to-end yet.

## Phase 31 — Tests

**Backend:** baseline 696 → final **753** (+57): 10 `test_token_verification.py`, 7
`test_user_resolution.py`, 7 `test_link_bootstrap_user.py`, 13 `test_credential_store.py`, 6
`test_api_me.py`, 14 `test_api_platforms_tiktok.py`. Zero existing tests modified; full suite re-run
confirmed 753 passed (`~/.venv/bin/python3 -m pytest -q` — note: must use the project's `.venv`, not a
bare `python3`, or `content_automation` fails to import).

**Frontend:** baseline 46 → final **67** (+21): 4 rewritten `session.test.tsx` cases (new Supabase-backed
contract, replacing the 3.5 mock-flag contract), 2 new `Button` action-mode cases, 5
`tests/lib/platforms.test.ts`, 3 `AppAuthGate.test.tsx`, 6 `settings-tiktok.test.tsx`, 4 `login.test.tsx`
— net +21 after replacing 3 of the original session tests. `npm run test` — 67 passed, 0 failed.

## Phase 32 — Documentation

Updated: `docs/architecture/hosted-product-boundary.md` (top-of-file note, §8, §12, §14, §17 addendum,
§18 "Authentication" subsection), this evaluation record, `AGENTS.md`, `CHANGELOG.md`, root `README.md`,
`web/README.md`, `.env.example` (root and `web/`). New ADR: `docs/decisions/0011-real-authentication-and-tiktok-connection.md`
— one ADR, not two, since the auth-provider decision and the TikTok-credential-storage decision are
tightly coupled (the latter only exists because of the former's identity model) and reading them
together is more useful than splitting them.

## Guardrails Respected

No batch-upload UI, upload workflow, cadence controls, real queue/calendar data, hosted scheduler/worker
orchestration, Instagram, YouTube, billing, analytics, caption intelligence, or native Expo app. No real
content was published to prove account connection — no real TikTok connection was even completed, since
real credentials were still being set up.

## Real Data

None touched. No real Google Cloud OAuth client, no real Supabase Auth configuration, and no real TikTok
OAuth exchange exist as of this record — all cryptographic/OAuth verification in this milestone's test
suite uses locally-generated, disposable keys/tokens/mocked TikTok responses, never live credentials.

## External Effects

None. No real email sent, no real OAuth consent screen shown to a real user, no real TikTok API call
made, no real deployment updated with real secrets.

## Deferred

- **Real Google Cloud OAuth client / Supabase Auth configuration:** setup was in progress with the user
  as of this writing (dashboard steps given; real values not yet received). Until complete,
  `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY`/`NEXT_PUBLIC_API_BASE_URL` remain unset in
  `netlify.toml`, and the deployed shell correctly fails closed rather than rendering broken.
- **`cli/link_bootstrap_user.py` run against real data:** requires a real `provider_subject`, which
  requires the setup above to be finished and one real login to happen first.
- **Live TikTok OAuth connection / real hosted publish:** not performed — no real TikTok credentials
  configured for the hosted path yet, and out of this milestone's explicit scope regardless.
- **Real Postgres exercise of the new tables:** `platform_credentials`/`oauth_states` were tested against
  SQLite only in this environment (no live `DATABASE_URL` available); the Postgres migration SQL and
  `PostgresContentStore` methods were written and import-checked but not run against a live database.
- **A distinct "reauthorization required" Settings UI state:** the backend doesn't currently distinguish
  this from "disconnected" in its status response.
- **A real distributed lock for hosted TikTok credential refresh:** the CAS-based mitigation closes the
  data-integrity risk but not a narrow TikTok-rotation race under true simultaneity — see ADR-0011.
- **A real API hosting provider:** `src/content_automation/api/` runs locally only.
- Batch upload, cadence/scheduling controls, real queue data, hosted workers, native app — all
  unchanged, future-milestone scope, per the brief's own guardrails.

## Required Final Report

**Milestone 3.6 — Real Authentication + TikTok Account Connection**

**Authentication**
- Provider: Supabase Auth, Google as the first provider.
- Rationale: same Supabase project as Postgres/Storage (real synergy, not chosen for consistency alone);
  server-verifiable JWTs without a per-request network call in the preferred mode; real React Native/Expo
  SDK; Google now, Apple later via config only.
- Token/session model: Supabase-issued JWT, PKCE flow client-side, bearer token per request server-side
  — no server-held session.
- Backend verification: `identity/token_verification.py`, JWKS (default) or HS256, both tested against
  real self-signed cryptography.

**User mapping**
- Provider identity: `auth_identities(provider, provider_subject)`, `UNIQUE`.
- `auth_identities` mapping: `identity/user_resolution.py`'s `resolve_or_create_user()` — no email
  auto-linking (`AccountConflictError` instead).
- Existing bootstrap user handling: `cli/link_bootstrap_user.py` — explicit, one-off, human-run; not yet
  executed against real data (blocked on real Supabase/Google setup completing).

**Mock-session removal**
- `DEV_MOCK_USER`: still exists, but only reachable outside a production build with Supabase
  unconfigured — a local-dev convenience, not a production path.
- Netlify flag: `NEXT_PUBLIC_ALLOW_MOCK_SESSION` deleted everywhere (code, `netlify.toml`, `.env.example`).
- Protected-route behavior: production build with Supabase unconfigured fails closed unconditionally —
  verified directly (a real `npm run build` without Supabase env vars fails; with placeholder-but-present
  values, it succeeds).

**Backend API**
- Auth endpoints/dependencies: `api/dependencies/auth.py#get_current_user`, used by every protected
  route; derives identity only from a verified bearer token.
- `/me`: `GET /api/me` → `{id, email, display_name}`.
- TikTok connection endpoints: `GET status`, `POST connect`, `GET callback` (public), `POST disconnect`.

**TikTok OAuth**
- Initiation: `POST /api/platforms/tiktok/connect` (protected), returns a real TikTok authorization URL.
- PKCE: reused verbatim from `publishing/tiktok/auth.py`, unmodified.
- State binding: a server-side `oauth_states` row bound to the authenticated caller's `user.id` at
  connect time — the callback (unauthenticated by necessity) trusts only that binding, never a header.
- Callback: `GET /api/platforms/tiktok/callback` — validates/atomically-consumes state, exchanges the
  code, persists the credential, redirects to `/app/settings?tiktok=<reason>`.
- Error handling: denied/invalid-state/expired-state/exchange-failed each redirect with a distinct,
  non-leaking reason — no raw TikTok error text or exception detail reaches the browser.

**Credential storage**
- Storage location: new `platform_credentials` table (SQLite + Postgres), separate from
  `platform_connections`.
- Encryption: Fernet (`cryptography`), `config.CREDENTIAL_ENCRYPTION_KEY`, server-side only.
- Refresh-token handling: `get_hosted_tiktok_access_token()` — CAS-based (optimistic concurrency) refresh,
  replacing the CLI's `fcntl` lock for the hosted path only; local CLI path untouched.
- Multi-user separation: `UNIQUE(platform_connection_id)`, one credential per connection, proven isolated
  between two real users in tests.

**Settings UI**
- Disconnected: real empty/inert state, "Connect" action enabled.
- Connecting: client-side transient state while the connect request is in flight.
- Connected: real account label shown, "Disconnect" action enabled.
- Error: both load-failure (retryable `ErrorState`) and action-failure (inline message) implemented; no
  distinct "reauthorization required" state yet (see Deferred).
- Disconnect: real, calls the backend, updates state on success.

**Tenant isolation**
- Status reads: proven independent per user.
- Credential writes: proven independent per user (two users, two real connect→callback cycles, distinct
  `account_label`s).
- Callback binding: proven attribution follows the state's own bound user, not whichever session is
  "current" when the callback fires.
- Disconnect: proven scoped to the caller's own connection only.

**Security**
- Client-supplied `user_id`: never accepted anywhere — identity is derived exclusively from the verified
  bearer token.
- Secrets in frontend: none found (`git grep`/`grep` review); `tests/lib/no-secrets-in-client-bundle.test.ts`
  continues to enforce this automatically going forward.
- Token leakage review: `/api/me` and TikTok status/connect/disconnect responses never include
  `access_token`/`refresh_token`/any credential field — enforced by narrow Pydantic response models.
- CORS: explicit origin allowlist, never a wildcard.

**Existing data**
- Videos preserved: yes — untouched (no real-data migration ran this milestone).
- Posts preserved: yes — untouched.
- Schedules preserved: yes — untouched.
- Storage references preserved: yes — untouched.

**Testing**
- Backend baseline: 696.
- Backend final: 753 (+57), 0 failed.
- Frontend baseline: 46.
- Frontend final: 67 (+21, after replacing 3 tests whose contract changed), 0 failed.

**Responsive validation**
- Widths: 320/375/390/768/1024/1440px (new `/login` route; `/app` redirect behavior confirmed separately
  in a real browser).
- Issues found/fixed: none found at `/login`; one real UI-primitives gap found and fixed (`Button` needed
  a real action mode instead of a disguised link for Connect/Disconnect — see Phase 18).

**Documentation**
- Architecture: `docs/architecture/hosted-product-boundary.md` updated (§8, §12, §14, §17, §18, top note).
- Evaluation: this record.
- ADRs: `docs/decisions/0011-real-authentication-and-tiktok-connection.md` (one, covering both the auth
  provider decision and the TikTok credential storage decision — tightly coupled, not split).

**Deferred**
- Batch upload: not built (Milestone 3.7's scope).
- Cadence: not built.
- Queue: not built; no real queue data wired.
- Hosted workers: not applicable to this milestone.
- Native app: not built; auth/API boundaries kept reusable for one (see Phase 10).
- Also deferred (not in the brief's own list, but real): real Google Cloud/Supabase setup completion,
  running `cli/link_bootstrap_user.py` against real data, a live TikTok OAuth connection, real Postgres
  exercise of the two new tables, a distinct "reauthorization required" UI state, a real distributed lock
  for hosted credential refresh, and a real API hosting provider.

**Overall**

**PARTIAL.** Every acceptance-criteria item that does not require real, user-supplied external
credentials is complete and verified: the auth provider is selected and documented (1), the server
verifies identity end to end with real cryptography (2), the mapping onto `users`/`auth_identities` is
real and tested (3), the mock session is gone from the production path with no bypass flag anywhere (4,
5), protected routes require a real session and fail closed (6), a client-supplied `user_id` is never
trusted anywhere in the codebase (7), the bootstrap-linking mechanism exists, is tested, and is safe to
run (8, not yet executed against real data), the hosted TikTok OAuth flow is built and its state-binding
security is proven (9, 10), credentials are stored per-connection and never exposed client-side (11, 12),
Settings shows real API-backed state (13), tenant isolation is proven with real users (14), auth/OAuth
failure states are handled cleanly (15), the existing backend publishing suite is unaffected — 753 passed
(16), and both test suites pass in full (17). What remains genuinely open, and is why this is PARTIAL
rather than COMPLETE, is entirely external to what this session could build: real Supabase/Google Cloud
setup was still in progress with the user as of this writing, so no real TikTok connection and no
real-Postgres run of the two new tables have happened yet. Closing those is a follow-up validation pass
once the user finishes that setup, not further implementation work.

Do not commit until reviewed.
