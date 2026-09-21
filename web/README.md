# Content Automation / Pickle Batch — Frontend

A Next.js (App Router, TypeScript, Tailwind CSS v4) app, deployed as a static export. Two parts, sharing
one Next.js app but no chrome: the public marketing site (Milestone 2.0.1) and, as of Milestone 3.5, a
real mobile-first product app shell that Milestones 3.6+ build the authenticated product UI inside. See
`docs/decisions/0010-frontend-app-shell.md` (repository root) for the full architecture decision record.

This directory is intentionally separate from the Python backend/CLI tooling at the repository root (`process_content.py`, `content_store.py`, etc.) — see the root `README.md`/`PROJECT_STATE.md` for that side of the project. No code is shared between them in either direction.

## Routes

| Route | Purpose |
|---|---|
| `/` | Homepage — product positioning, how it works, current capabilities, platform roadmap, contact CTA. |
| `/privacy` | Privacy Policy. |
| `/terms` | Terms of Service. |
| `/app` | Product home/dashboard — quick links into Library, Queue, Settings. |
| `/app/library` | Videos you've batched/processed (real empty state by default — batch upload is Milestone 3.7's scope). |
| `/app/queue` | What's scheduled/published (real empty state by default — real queue data is a future milestone's scope). |
| `/app/settings` | Account + connected-platform status (TikTok OAuth itself is Milestone 3.6's scope). |

**`/app/*` currently runs on a development-only mock session** (`lib/session.tsx`) — there is no real
authentication yet. See "Auth / Session" below before touching anything under `/app`.

## Development

```bash
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run test     # Vitest + React Testing Library — see tests/
npm run build    # static export -> out/
```

## Auth / Session

`lib/session.tsx`'s `useSession()` is the only supported way to read "who is the current user" anywhere
in `/app/*` — never import `DEV_MOCK_USER` directly, and never add a second session mechanism. The mock
session fails closed in a production build unless `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` is explicitly
set (see `.env.example`); the deployed Netlify build currently sets it deliberately, as a temporary
shell-development flag — **not** a decision about how real authentication will work. Before any real
user-owned data or authenticated API call is wired into `/app`, see
`docs/decisions/0010-frontend-app-shell.md`'s "BLOCKER BEFORE REAL USER DATA ACCESS" checklist.

## API boundary

`lib/api/client.ts`'s `apiRequest()` is the only place this app should ever call a backend HTTP
endpoint — no backend exists yet, so nothing calls it today; product pages read `lib/api/mockData.ts`
instead, or ship genuinely empty. `NEXT_PUBLIC_API_BASE_URL` (see `.env.example`) is the only
backend-related environment variable this app reads; anything server-only (a database URL, a
service-role key) has no reason to exist in this package at all — see
`tests/lib/no-secrets-in-client-bundle.test.ts`.

## Design tokens

Colors and fonts are centralized in `app/globals.css`'s `:root`/`@theme` block — that's the one place to change the site's look (background, surface, ink/text, border, accent). Components reference these via Tailwind utilities (`bg-accent`, `text-ink-muted`, etc.), never a hardcoded hex value. Site-wide text/contact details (name, tagline, description, contact email) live in `lib/site-config.ts`.

**`lib/site-config.ts`'s `contactEmail` is a placeholder** (`support@content-automation.app`) — replace it with a real, monitored address before deploying this publicly; `/privacy` and `/terms` both reference it as a real contact method.

## Deployment (Netlify)

Deployed via the repository-root `netlify.toml` (`base = "web"`, static export from `out/`). No Netlify Next.js runtime plugin needed — there are no server-rendered/dynamic routes yet. See the root `README.md` for the exact steps and the resulting URLs needed for TikTok Sandbox configuration.

`netlify.toml` currently sets `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` — a deliberate, temporary flag letting the `/app` shell's development-only mock session render in production (see "Auth / Session" above). Do not remove it without first replacing `lib/session.tsx`'s mock path with real authentication.

## Structure

```
app/(marketing)/     public routes (/, /privacy, /terms) — own chrome via app/(marketing)/layout.tsx
app/app/              product app shell (/app, /app/library, /app/queue, /app/settings)
components/ui/       generic reusable primitives (Button, Card, Badge, EmptyState, ErrorState, Spinner, PageHeader, Container)
components/layout/   marketing site header/footer
components/sections/ homepage sections
components/app/      product shell nav/header/queue-row components
lib/                 site-config.ts, api/ (client, types, mock data), session.tsx, status.ts
tests/               Vitest + React Testing Library — routes/, components/, lib/
```

Follows the Next.js App Router folder convention from this project's global engineering conventions (`components/ui`, `components/layout`, `components/sections`, `components/forms` when needed, `lib/`, `hooks/`, `types/`). See `docs/decisions/0010-frontend-app-shell.md` (repository root) for why the route/API-client/session boundaries are shaped this way.
