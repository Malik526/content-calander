# Native / iOS Readiness (Milestone 3.15)

What Milestone 3.15 establishes so that the native app (Milestone 7, **iOS / App Store first**) is
an additional client of the existing backend, not a backend rewrite. Nothing native is built yet.

```text
Next.js web (static export) ─┐
                             ├── FastAPI (src/content_automation/api/) ── Postgres / Supabase Storage / worker
Expo / React Native (iOS) ───┘
```

## Ownership split

**Shared backend (unchanged by a native client):** bearer-JWT auth verification
(`identity/token_verification.py`), users and identities, videos and canonical captions, object
storage, cadence and slot generation, Queue assignment, platform connections and encrypted
credentials, publishing, the hosted worker, reconciliation and recovery, and all publish-state
derivation (`publishing/publish_status.py`). Business logic stays here.

**Web-specific (`web/`):** Next.js routing and the static-export shell, browser navigation, the
web Supabase session (`lib/session.tsx`, browser storage via supabase-js), the TikTok connect
redirect (`window.location` in `app/app/settings/page.tsx`) and its `?tiktok=` return handling,
HTML file input / `FormData` from `File` objects, browser metadata (theme-color, manifest).

**Future native-specific:** Expo / React Native UI, an iOS Supabase auth adapter (native Google
sign-in, later Sign in with Apple), secure token storage (Keychain via `expo-secure-store`), the
native media picker, OAuth return through `ASWebAuthenticationSession` / deep links, push
notifications, App Store lifecycle (review, versioning, forced-upgrade policy).

## Reusable client layers in `web/` today

| Layer | Portable? | Notes |
|---|---|---|
| `lib/api/client.ts` | Yes | `fetch` + Bearer token, no cookies or browser storage. `createApiRequest(baseUrl)` lets a native client supply its own base URL (e.g. `EXPO_PUBLIC_API_BASE_URL`). |
| `lib/api/{videos,queue,cadence,captions,platforms}.ts` | Yes, except upload | Plain typed calls taking a token. `uploadVideos` takes browser `File`s (see Upload). |
| `lib/api/types.ts` | Yes | Hand-mirrored from `api/schemas/*` (no codegen yet). |
| `lib/domain/publishing.ts` | Yes | Publish-status vocabulary and platform ids, no framework imports. |
| `lib/query/keys.ts`, `hooks/` | Yes (pattern and code) | TanStack Query runs in React Native; hooks depend only on the session's user id and token. |
| `lib/status.ts`, `lib/ui-state.tsx`, components | Web presentation | Reproduce in native UI; reuse the rules, not the markup. |

Contract strategy: the FastAPI Pydantic schemas are canonical; `lib/api/types.ts` mirrors them
and `lib/domain/` holds shared vocabulary. If the native client starts, generating TypeScript
types from FastAPI's OpenAPI document (`/openapi.json`) is the natural next step, replacing the
hand-mirrored types for both clients.

## Authentication

Expected native flow:

```text
iOS app → Supabase native auth (Google, later Apple) → Supabase access token (JWT)
        → Authorization: Bearer <token> on every FastAPI request
        → backend verifies the same JWT (JWKS) and resolves the same user (auth_identities)
```

Findings:

- The backend never depends on the web frontend for identity: no cookies, no session endpoint,
  no origin checks. `get_current_user` reads only the bearer token. The same Supabase user signing
  in from iOS resolves to the same `users` row.
- CORS (`API_CORS_ALLOWED_ORIGINS`, `allow_credentials=True`) only constrains browsers; native
  requests are unaffected. Credentials mode is unused by the web client (bearer header).
- Native must refresh tokens itself (supabase-js in React Native with secure storage does this)
  and handle a 401 by refreshing/re-authenticating.
- Before App Store submission, Sign in with Apple is required if Google sign-in is offered (App
  Store Review Guideline 4.8), so it is planned as a Supabase provider addition with no backend
  change.

No blocking assumption found; no code change needed.

## Platform OAuth (TikTok today, Instagram/YouTube next)

Current flow (`api/routes/platforms_tiktok.py`):

1. `POST /api/platforms/tiktok/connect` (bearer-authenticated) creates a server-side
   `oauth_states` row (user, PKCE verifier, redirect URI) and returns `authorization_url`.
   **Client-neutral already:** it returns a URL rather than redirecting, so any client can open it.
2. The browser goes to TikTok; TikTok redirects to `TIKTOK_WEB_REDIRECT_URI`, the backend
   callback. The callback is unauthenticated and identifies the user by `state` only, which also
   works when a native system browser completes the flow.
3. **Web-specific:** the callback always redirects to `FRONTEND_BASE_URL/app/settings?tiktok=<reason>`.

Native plan (Milestone 7):

```text
app → POST /connect (with a client/return hint) → open authorization_url in ASWebAuthenticationSession
    → TikTok → backend callback (unchanged token exchange)
    → backend redirects to the client's return target (e.g. picklebatch://oauth/tiktok?result=connected
      or a universal link)
    → session closes → app refetches GET /api/platforms/tiktok/status
```

The one backend change needed: store an allowlisted return target on the `oauth_states` row at
connect time and use it in `_settings_redirect`, defaulting to today's web URL. **Deferred to
Milestone 7**, not done now: it changes the security-sensitive OAuth path, needs an allowlist and
deep-link/universal-link setup to test, and the web flow is validated in production. The web
client side is already separated: `useTikTokActions().startConnect()` returns the URL, and only
the Settings page (web adapter) decides to navigate to it.

**Instagram (Milestone 4.x):** the Instagram connect flow is designed native-ready from the start
(ADR-0018 Decision 6). 4.1 adds a nullable, allowlisted `oauth_states.return_target`, chosen at
connect time, and the Instagram callback redirects there (default: web Settings). Milestone 7 then
only adds native return targets, and moves TikTok onto the same column.

Platform registration note: TikTok redirect URIs must be HTTPS; native apps keep the backend
callback and use a custom scheme or universal link only for the final hop back into the app.

## Upload

Current contract: `POST /api/videos`, `multipart/form-data`, one or more parts named `files`,
bearer auth. The backend validates by extension (`.mp4`, `.mov`), streams each part to a temp
file, hashes it, uploads it to object storage and returns one `VideoUploadResult` per file
(`api/schemas/videos.py`). Every upload creates a new video row; there's no duplicate rejection.

Findings:

- Nothing is HTML-form-specific on the server: any multipart client works. React Native's
  `FormData` accepts `{ uri, name, type }` parts from the media picker, so the native adapter is a
  few lines. Only `lib/api/videos.ts`'s `uploadVideos(files: FileList | File[])` signature is
  browser-typed; native gets its own adapter for the same endpoint.
- iOS camera roll exports may be `.mov` (HEVC) or `.mp4` named by the picker; the native adapter
  must send a filename with a supported extension. MIME type isn't used server-side.
- No upload progress today: `fetch` doesn't report it. Native can use
  `expo-file-system`'s `uploadAsync` or `XMLHttpRequest` for progress against the same endpoint;
  the web could adopt `XMLHttpRequest` the same way. No API change needed.

Future scalability concern (not a 3.15 change): uploads stream through the API process, so large
4K videos over cellular tie up an API worker for the whole transfer, with no resume. If that
becomes a problem, add direct-to-storage signed uploads (Supabase Storage signed upload URLs or
TUS resumable uploads) with a "complete upload" API call that creates the video row. It would
serve web and native equally. Decide with upload-telemetry data (`upload_attempts`), not ahead of
it.

## Explicitly deferred (not in 3.15)

Expo project, React Native screens, App Store submission, Sign in with Apple, native Google
sign-in, push notifications, deep/universal links, offline-first sync, service worker / PWA,
OpenAPI type generation, direct-to-storage uploads, and the OAuth return-target change above.
