# Content Automation / Pickle Batch — Frontend

A Next.js (App Router, TypeScript, Tailwind CSS v4) app, deployed as a static export. Two parts, sharing
one Next.js app but no chrome: the public marketing site (Milestone 2.0.1) and, as of Milestone 3.5, a
real mobile-first product app shell — now (Milestone 3.6) backed by real authentication and a real
backend API. See `docs/decisions/0010-frontend-app-shell.md` and
`docs/decisions/0011-real-authentication-and-tiktok-connection.md` (repository root) for the full
architecture decision records.

This directory is intentionally separate from the Python backend/CLI tooling at the repository root (`process_content.py`, `content_store.py`, etc.) — see the root `README.md`/`PROJECT_STATE.md` for that side of the project. No code is shared between them in either direction.

## Routes

| Route | Purpose |
|---|---|
| `/` | Homepage — product positioning, how it works, current capabilities, platform roadmap, contact CTA. |
| `/privacy` | Privacy Policy. |
| `/terms` | Terms of Service. |
| `/login` | Sign in with Google (Supabase Auth). Public. |
| `/auth/callback` | Where Google/Supabase redirects back to after sign-in; navigates into `/app` once a session exists. Public. |
| `/app` | Product home/dashboard — quick links into Library, Queue, Settings. Requires a real session. |
| `/app/library` | Real batch video upload (Milestone 3.7) + your own videos, backend-verified (`GET/POST /api/videos`) — live validation with real video files against the deployed stack is still outstanding, see `docs/evaluations/productization/milestone-3.7-batch-upload-readiness.md`. |
| `/app/queue` | What's scheduled/published (real empty state by default — real queue data is a future milestone's scope). |
| `/app/settings` | Account (real session) + real, backend-verified TikTok connection status/connect/disconnect. |

**`/app/*` requires a real signed-in session** (`lib/session.tsx`, real Supabase Auth as of Milestone
3.6) — an unauthenticated visitor is redirected to `/login` client-side. See "Auth / Session" below.

## Development

```bash
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run test     # Vitest + React Testing Library — see tests/
npm run build    # static export -> out/ (requires NEXT_PUBLIC_SUPABASE_URL/ANON_KEY set — see below)
```

To also exercise the real backend locally: `python3 cli/run_api.py` (repository root, inside `.venv`) —
see the root `README.md`'s "Frontend (Public Website + Product App Shell)" section.

## Auth / Session

`lib/session.tsx`'s `useSession()` is the only supported way to read "who is the current user" anywhere
in `/app/*` — never a second session mechanism. As of Milestone 3.6, it wraps real Supabase Auth
(`lib/supabase/client.ts`, Google as the first provider) — there is **no bypass flag anymore**
(`NEXT_PUBLIC_ALLOW_MOCK_SESSION` was removed entirely). A production build with
`NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` unset fails closed unconditionally. The only
remaining fallback — a loudly-marked `DEV_MOCK_USER` — only ever applies outside a production build
*and* only when Supabase is unconfigured, purely a local-dev convenience. See
`docs/decisions/0011-real-authentication-and-tiktok-connection.md` "Mock session removed."

## API boundary

`lib/api/client.ts`'s `apiRequest()` is the only place this app calls a backend HTTP endpoint — no
component calls `fetch()` directly. As of Milestone 3.6 a real backend exists
(`src/content_automation/api/`, repository root) and `lib/api/platforms.ts`'s typed functions
(`getMe`, `getTikTokConnection`, `connectTikTok`, `disconnectTikTok`) call it, attaching the current
session's access token as `Authorization: Bearer <token>`. `NEXT_PUBLIC_API_BASE_URL` (see
`.env.example`) is the backend's base URL; `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY`
are the only other backend-related environment variables this app reads — both meant to be public.
Anything server-only (a database URL, a service-role key, `SUPABASE_JWT_SECRET`,
`CREDENTIAL_ENCRYPTION_KEY`) has no reason to exist in this package at all — see
`tests/lib/no-secrets-in-client-bundle.test.ts`.

## Design tokens

Colors and fonts are centralized in `app/globals.css`'s `:root`/`@theme` block — that's the one place to change the site's look (background, surface, ink/text, border, accent). Components reference these via Tailwind utilities (`bg-accent`, `text-ink-muted`, etc.), never a hardcoded hex value. Site-wide text/contact details (name, tagline, description, contact email) live in `lib/site-config.ts`.

**`lib/site-config.ts`'s `contactEmail` is a placeholder** (`support@content-automation.app`) — replace it with a real, monitored address before deploying this publicly; `/privacy` and `/terms` both reference it as a real contact method.

## Deployment (Netlify)

Deployed via the repository-root `netlify.toml` (`base = "web"`, static export from `out/`). No Netlify Next.js runtime plugin needed — there are no server-rendered/dynamic routes yet. See the root `README.md` for the exact steps and the resulting URLs needed for TikTok Sandbox configuration.

`netlify.toml` does **not** currently set `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY`/
`NEXT_PUBLIC_API_BASE_URL` — set them via Netlify's dashboard (Site configuration -> Environment
variables) once real Supabase/API values exist. Until then, the deployed `/app` shell correctly fails
closed rather than rendering broken (see "Auth / Session" above).

## Structure

```
app/(marketing)/     public routes (/, /privacy, /terms) — own chrome via app/(marketing)/layout.tsx
app/login/            sign-in page (Google via Supabase Auth)
app/auth/callback/    OAuth redirect target
app/app/              product app shell (/app, /app/library, /app/queue, /app/settings) — auth-gated
components/ui/       generic reusable primitives (Button, Card, Badge, EmptyState, ErrorState, Spinner, PageHeader, Container)
components/layout/   marketing site header/footer
components/sections/ homepage sections
components/app/      product shell nav/header/queue-row/auth-gate components
lib/                 site-config.ts, api/ (client, types, platforms.ts, mock data), session.tsx, status.ts, supabase/client.ts
tests/               Vitest + React Testing Library — routes/, components/, lib/
```

Follows the Next.js App Router folder convention from this project's global engineering conventions (`components/ui`, `components/layout`, `components/sections`, `components/forms` when needed, `lib/`, `hooks/`, `types/`). See `docs/decisions/0010-frontend-app-shell.md`/`0011-real-authentication-and-tiktok-connection.md` (repository root) for why the route/API-client/session boundaries are shaped this way.
