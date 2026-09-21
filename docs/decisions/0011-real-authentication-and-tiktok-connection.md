# ADR-0011: Real Authentication and Hosted TikTok Account Connection

## Status

Accepted

## Context

Milestone 3.5 built the mobile-first product app shell (`web/app/app/*`) on a deliberately temporary,
loudly-marked mock session (`DEV_MOCK_USER`), explicitly gated behind a Netlify build flag
(`NEXT_PUBLIC_ALLOW_MOCK_SESSION=true`) — see ADR-0010's "BLOCKER BEFORE REAL USER DATA ACCESS." That
blocker's own checklist is this milestone's actual scope: implement real, server-verifiable
authentication; replace the mock `SessionProvider` path; remove the Netlify flag; verify the production
build fails closed without it; never trust a client-supplied `user_id`. Once real identity exists, this
milestone also builds the first real user-owned product integration — connecting a TikTok account from
the web app — since there is no other real per-user data worth protecting yet to prove the auth
boundary against.

This had to happen without redesigning existing TikTok OAuth semantics (`publishing/tiktok/auth.py`,
proven across Milestones 2.0–2.1.10), without disturbing local CLI ingestion/scheduling/publishing
behavior at all, and without building batch upload, real scheduling controls, real queue data, or hosted
workers — all explicitly out of scope per the brief's own guardrails.

## Decision

### Authentication provider: Supabase Auth, evaluated on its own merits

Evaluated against Auth.js/NextAuth (ruled out — fundamentally a Next.js-server-oriented library with no
real React Native story, directly conflicting with the requirement to keep the auth architecture
native-compatible), Firebase Auth/Clerk/Auth0 (ruled out — each adds a second vendor with no synergy
with the Postgres user model already in place, for no concrete advantage), and rolling a custom
Google-Identity-Services + self-issued-JWT flow (ruled out — reinvents session/token issuance and
refresh Supabase Auth already provides correctly). Supabase Auth won because: Postgres and Storage
already live in the same Supabase project (ADR-0008/0009 — not chosen for consistency alone, but real
synergy); it issues JWTs verifiable server-side without a per-request network call in the preferred mode
(see "Token Verification" below); it has a real React Native/Expo SDK, satisfying the future-native-
compatibility requirement directly; it supports Google now and Apple later via dashboard configuration
alone; and its `sub` claim maps cleanly onto the `auth_identities.provider_subject` pattern ADR-0007
already established. Google is the first (only) provider wired up this milestone.

**Choosing Supabase Auth did not mean the backend trusts Supabase's session cookies or SDK state** — the
backend only ever verifies a bearer token presented on each request (see below); it holds no session of
its own.

### Token verification: JWKS-preferred, HS256 supported, never a bypass

`identity/token_verification.py`'s `verify_access_token()` supports two modes
(`config.SUPABASE_AUTH_JWT_MODE`): `"jwks"` (default) verifies against Supabase's public asymmetric
signing keys via `PyJWKClient` (cached in-process by key id — no network round trip per request after
the first), requiring no shared secret server-side at all; `"hs256"` verifies against
`config.SUPABASE_JWT_SECRET` (a real shared secret, server-side only, never logged or exposed
client-side) for Supabase projects still on legacy JWT signing. Every failure mode (expired, wrong
signature, wrong audience, missing `sub` claim, or the server itself missing required configuration)
raises `TokenVerificationError`, translated to HTTP 401 by `api/dependencies/auth.py` — there is no
partially-trusted result and no fallback identity. Verified directly against real cryptography, not
mocked away: `tests/test_token_verification.py` generates a real RSA keypair, signs a real JWT against
it, and proves both successful verification and every rejection path (expired, forged signature, wrong
audience, missing claim) against the actual verification code.

### User / Auth Identity Mapping: no email auto-linking

`identity/user_resolution.py`'s `resolve_or_create_user()` is the login-time entry point: look up
`auth_identities` by `(provider, provider_subject)`; if found, return that user (login); if not, this is
a first-time signup — create a `users` row and link it. **Deliberately does not merge by matching
email** — a verified identity whose email matches an existing, differently-linked `users` row raises
`AccountConflictError` rather than silently attaching to that account. Auto-linking by email is a real
security footgun (email reuse/typosquatting across identity providers); this milestone does not take
that risk on for a benefit ("fewer accidental duplicate accounts") that doesn't apply yet, since Google
is the only provider. A known, accepted, documented residual imperfection: two truly concurrent
first-logins for the same brand-new identity can leave one harmless orphaned `users` row (no linked
`auth_identity`, so never reachable) rather than true cross-statement atomicity — `auth_identities`'
own `UNIQUE(provider, provider_subject)` constraint is what actually prevents two accounts from ending
up linked to the same external identity; building full transactional wrapping for this narrow race was
judged not justified by this milestone's brief.

### Existing bootstrap user: an explicit, one-off, human-run link — never automatic

The pre-3.6 local bootstrap user (`local@pickle-batch.local`, ADR-0007) owns every real production row —
5 videos, content_slots, platform_posts, and the existing TikTok `platform_connection`. Letting the
operator's first real login silently resolve to a brand-new account (the normal `resolve_or_create_user`
behavior) would orphan all of that data. `cli/link_bootstrap_user.py` closes this gap: a manual, one-off
script that creates exactly one `auth_identities` row pointing `(provider, provider_subject)` at the
*existing* bootstrap user's id — never automatic, never inferred, requiring the operator to already know
their own verified `provider_subject` (obtained by logging in once and reading the resulting token, or
via Supabase's dashboard). It refuses (does not silently proceed) if the bootstrap user doesn't exist
yet, if the identity is already linked to a *different* user, or if the bootstrap user already has a
*different* identity linked for the same provider — matching this repository's established migration-
script conventions (dry-run support, verify before mutating, never delete/reassign silently). No real
data was migrated by writing this ADR; running it against real data is a separate, explicitly-confirmed
action, per this repository's standing real-data-safety policy.

### Mock session removed — no bypass flag anywhere, in either direction

`lib/session.tsx`'s `SessionProvider` now wraps real Supabase Auth (`lib/supabase/client.ts`,
`@supabase/supabase-js`, PKCE flow) instead of always returning `DEV_MOCK_USER`.
`NEXT_PUBLIC_ALLOW_MOCK_SESSION` was removed from `lib/session.tsx`, `netlify.toml`, and
`web/.env.example` entirely — there is no flag left anywhere that re-enables the mock in a production
build. The one remaining fallback: if Supabase env vars are unset AND this is *not* a production build
(`NODE_ENV !== "production"`), `SessionProvider` still renders the same loudly-marked `DEV_MOCK_USER` —
purely a local-development convenience (work against the shell before Supabase is configured), computed
synchronously during the component's initial render (a `useState` lazy initializer, not inside an
effect) so a production build with Supabase unconfigured fails immediately and unconditionally. Proven
directly by `tests/lib/session.test.tsx`: dev fallback renders outside production; production without
Supabase configured throws; a real (mocked) Supabase session correctly resolves to
`authenticated`/`unauthenticated`. `/app/*` is additionally gated client-side
(`components/app/AppAuthGate.tsx`, redirects to `/login` when unauthenticated) — explicitly a UX
affordance, not the security boundary, since this is a static export with no server-side session check
possible; the backend API's own independent token verification on every request is the actual boundary.

### Backend API surface: the minimum needed, nothing generic

`src/content_automation/api/` (FastAPI, new — the location `hosted-product-boundary.md` §12 predicted)
implements exactly four endpoints: `GET /api/me`, `GET /api/platforms/tiktok/status`,
`POST /api/platforms/tiktok/connect`, `GET /api/platforms/tiktok/callback`,
`POST /api/platforms/tiktok/disconnect` (plus an unauthenticated `/api/health`). No generic CRUD, no
batch-upload/scheduling/queue endpoints — those remain future milestones' scope. `api/dependencies/auth.py`'s
`get_current_user` is the one dependency every protected route uses, deriving identity exclusively from
a verified bearer token — **never from a client-supplied `user_id`** anywhere (query param, header, or
body). The store dependency (`get_store`) opens the runtime-configured backend
(`persistence.store_factory.build_content_store()` — Postgres if `DATABASE_URL` is set, SQLite
otherwise) fresh per request, matching this codebase's established no-silent-fallback selection
philosophy and satisfying the requirement to never accidentally instantiate the local/dev backend for an
authenticated production request. CORS is an explicit origin allowlist (`config.API_CORS_ALLOWED_ORIGINS`),
never a wildcard.

### TikTok OAuth: the hosted redirect flow, reusing proven logic verbatim

`publishing/tiktok/auth.py`'s PKCE generation, state generation, authorization-URL building, and token
exchange/refresh (`generate_state`, `generate_pkce_pair`, `build_authorization_url`,
`exchange_code_for_token`, `refresh_access_token`) are reused **completely unmodified** — this milestone
never re-implements TikTok's OAuth wire protocol, only adds where the hosted flow's callback lands and
where the resulting credential is stored. The existing local CLI flow (`authorize_interactive`, the
hand-rolled localhost `http.server`, the local token file, the `fcntl` refresh lock) is untouched and
remains the fully-supported local-development path — both paths coexist by design.

**Flow:** authenticated user clicks Connect → `POST /api/platforms/tiktok/connect` generates a fresh
state/PKCE pair, persists a server-side `oauth_states` row bound to the caller's verified `user.id`
(`redirect_uri` resolved from the request itself via `request.url_for("tiktok_oauth_callback")`, so it's
correct in both local dev and any real deployment without hardcoding a host), and returns the real
TikTok authorization URL for the browser to navigate to. TikTok redirects the browser to
`GET /api/platforms/tiktok/callback` — deliberately **unauthenticated** (a top-level browser navigation
TikTok controls carries no `Authorization` header the frontend's client would normally attach). The
binding to "which user does this belong to" comes entirely from `oauth_states.user_id`, set while the
caller *was* authenticated — never from anything the callback request itself carries. This is the
concrete implementation of "OAuth state must securely bind the callback to the authenticated user" and
"never let the browser decide which user_id receives the credential." `consume_oauth_state()` atomically
marks the state used (a compare-and-swap `UPDATE ... WHERE consumed_at IS NULL`) — a replayed, unknown,
or expired state is rejected identically (`?tiktok=expired_state`), never distinguished in a way that
would help an attacker. A denial or error from TikTok itself, an invalid state, and a token-exchange
failure each redirect back to `/app/settings` with a distinct-but-non-leaking `?tiktok=<reason>` query
param — no raw TikTok error detail, stack trace, or credential ever reaches the browser.

### Credential storage: a new table, application-level encryption, CAS-based hosted refresh

`platform_credentials` (new table, SQLite + Postgres migration `0003_...sql`) stays **separate** from
`platform_connections`, which ADR-0007 already documented to carry no credential secrets — identity/
status stays there; the encrypted access/refresh token pair lives only in the new table,
`UNIQUE(platform_connection_id)`. Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`
— already a transitive dependency, not a new one) under `config.CREDENTIAL_ENCRYPTION_KEY` (server-side
only, generated once, never a default, never logged). This — not a KMS/secrets-manager integration — is
the right-sized mechanism for this milestone: a managed secrets service is real future infrastructure,
not something this milestone's actual requirement (don't store a hosted refresh token in plaintext)
justifies building yet.

**Concurrency:** `get_hosted_tiktok_access_token()` (`publishing/tiktok/credential_store.py`) is the
hosted analog of `auth.get_access_token()`, replacing its process-local `fcntl` lock (wrong for a hosted
API, where two separate request-handling processes might race) with a bounded optimistic-concurrency
(CAS) retry loop against `platform_credentials.updated_at` — the same `update_platform_post_if_unchanged`
pattern already proven elsewhere in this codebase. This fully closes the *data-integrity* risk (two
concurrent refreshes can never both persist, corrupting or duplicating a credential row) but has one
honestly-documented residual limitation: two genuinely simultaneous refresh attempts could both call
TikTok's own refresh endpoint with the same (soon-to-be-rotated) refresh token before either persists,
and TikTok's own rotation behavior might then reject the second real HTTP call outright — a real
distributed lock (e.g. a Postgres advisory lock) would close this fully; not built this milestone, per
Phase 15's own explicit allowance to document rather than solve this specific remaining edge for a
per-user (not shared/global) credential where true simultaneity is expected to be rare. Verified directly,
including a real simulated lost-CAS-race scenario, in `tests/test_credential_store.py`.

### Disconnect: credential removal, connection history preserved

`POST /api/platforms/tiktok/disconnect` deletes the row in `platform_credentials` and sets the
`platform_connections` row's `status` to `DISCONNECTED` (a new `update_platform_connection_status`
method — unconditional, since a disconnect is always one explicit authenticated action, not a background
refresh race) — it never deletes the `platform_connections` row itself, preserving identity/history. No
provider-side token revocation call was implemented; TikTok's current OAuth documentation does not
publish a distinct revoke endpoint this codebase could target confidently, and the credential secret is
deleted locally regardless, which is the actionable security property that matters here.

### Tenant isolation and security review

Proven directly with two real users (A and B) across the full connect → callback → status → disconnect
cycle (`tests/test_api_platforms_tiktok.py`): each user's TikTok connection is completely independent;
disconnecting one never affects the other; a state bound to user A's connect attempt is redeemed for
user A's connection regardless of which "current session" happens to hit the callback (since the
callback route consults no session at all, only the state's own binding). A `git grep`/`grep` security
review confirmed no `DATABASE_URL`/service-role/JWT-secret/credential-encryption-key pattern exists
anywhere in `web/` outside comments explicitly warning against it — the existing
`tests/lib/no-secrets-in-client-bundle.test.ts` guardrail from Milestone 3.5 continues to enforce this
automatically. `GET /api/me` and the TikTok status/connect/disconnect responses never include
`access_token`/`refresh_token`/any credential-shaped field — enforced by narrow, explicit Pydantic
response models (`api/schemas/`), not `return record.__dict__`.

### Future native compatibility

Nothing about this design is web-only: Supabase Auth's session/token model works identically from a
React Native/Expo client (same SDK family), the backend's `verify_access_token()` has no dependency on
cookies or a browser-specific mechanism (a bearer token is a bearer token regardless of client), and
`lib/api/platforms.ts`'s typed functions take an access token as an explicit parameter rather than
reading it from a web-only source — a future native client could reuse the exact same functions against
the same backend.

## Consequences

- Any future second identity provider (Apple, email/magic-link) adds a second accepted `provider` value
  to `api/dependencies/auth.py`'s `AUTH_PROVIDER` handling and a second Supabase dashboard provider
  configuration — not a new dependency or a new verification code path.
- Any future second publishable platform (Instagram, YouTube) should follow this exact
  `platform_connections` (identity/status) + `platform_credentials` (encrypted secret) split, and the
  same state-bound hosted-OAuth-callback pattern — not a bespoke per-platform mechanism.
- The real distributed-lock gap in hosted TikTok credential refresh (see "Credential Storage") remains
  open — the same category of residual risk `hosted-product-boundary.md`'s migration-risk list already
  tracks for `tiktok_auth._refresh_lock()`'s host-local `fcntl` lock under real hosted multi-worker
  execution. Whichever milestone introduces real hosted background workers should close both at once,
  not treat them as separately solved.
- `cli/link_bootstrap_user.py` running against real production data is a separate, explicitly-confirmed
  action — not performed by writing this ADR or by implementing the script itself.
- `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY`/`NEXT_PUBLIC_API_BASE_URL` are not set in
  `netlify.toml` as of this writing — real values require finishing the Supabase/Google Cloud setup this
  milestone's own work depends on; until they're set, the deployed shell correctly fails closed rather
  than silently rendering broken (see `netlify.toml`'s own comment).
- No FastAPI backend hosting provider was selected — `src/content_automation/api/` runs locally
  (`cli/run_api.py`) for now; `hosted-product-boundary.md` §14's "API hosting: deferred" row is still
  accurate.
