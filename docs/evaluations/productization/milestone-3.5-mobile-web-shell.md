# Milestone 3.5 — Mobile-First Responsive Web App Shell

## Objective

Build a production-quality, mobile-first, reusable frontend **app shell** in `web/` on top of the
completed backend (Milestones 3.0-3.4) — not a full product yet, just the shell/architecture
Milestones 3.6-3.9 build the real product UI inside. Explicit guardrails: no real auth provider
integration, no TikTok connection UI, no batch upload, no real scheduling controls, no real
queue/calendar backend data, no hosted workers, no billing/analytics/native app.

## Frontend State Before This Milestone

`web/` (Milestone 2.0.1) was a public marketing site only: `/`, `/privacy`, `/terms`, a single shared
root layout owning `SiteHeader`/`SiteFooter`, `components/ui/{Button,Container}`,
`components/layout/{SiteHeader,SiteFooter}`, `components/sections/*`, `lib/site-config.ts`. No
authenticated surface, no API-client boundary, no session concept, no design tokens beyond the
marketing palette, no test framework, no PWA metadata. Deployed to Netlify via the repo-root
`netlify.toml` as a static export.

## Framework Decision

No framework change — extended the existing Next.js 16 (App Router, TypeScript, Tailwind CSS v4,
static export) app rather than starting a new project or introducing a second frontend. See
`docs/decisions/0010-frontend-app-shell.md` for the full reasoning.

## Route Architecture

Two Next.js route groups, no shared chrome:

- `app/(marketing)/` — `/`, `/privacy`, `/terms` (unchanged URLs; moved via `git mv` from the
  previous flat `app/` layout so git history for each page is preserved). Owns its own layout
  (`app/(marketing)/layout.tsx`) rendering `SiteHeader`/`SiteFooter`.
- `app/app/` — `/app`, `/app/library`, `/app/queue`, `/app/settings` (new). Owns its own layout
  (`app/app/layout.tsx`) rendering `AppHeader`/`AppSideNav`/`AppBottomNav`, wrapped in
  `SessionProvider`.

The true root layout (`app/layout.tsx`) was cut down to only genuinely global concerns — `<html>`/
`<body>`, font, and `metadata`/`viewport` defaults — so the two sections can diverge completely
without either fighting the root layout's assumptions.

## App Shell

- **Mobile navigation:** `AppBottomNav` — a fixed bottom tab bar (`md:hidden`), four destinations
  (Home/Library/Queue/Settings), `env(safe-area-inset-bottom)` padding for iOS home-indicator
  clearance, `min-h-14` touch targets.
- **Desktop navigation:** `AppSideNav` — a left sidebar (`hidden md:block`), the same `navItems`
  list and `isActive()` logic as the mobile nav, rendered differently, not maintained separately.
- **Content layout:** `app/app/layout.tsx`'s content column gets `pb-20` on mobile specifically so
  the fixed bottom nav never overlaps the last visible content; `max-w-5xl` container, narrower
  `--width-app-content` token available for individual pages that want it.

## Responsive Behavior

**Breakpoints:** Tailwind's default `sm`/`md` (640px/768px) — `md` is where mobile nav gives way to
desktop side nav; `sm` is where a handful of components (`PageHeader`, the settings
platform-connection row) switch from stacked to row layout.

**Widths manually validated:** 320, 375, 390, 768, 1024, 1440px — against all 7 real routes (the 4
product routes plus `/`, `/privacy`, `/terms`), using a real headless Chromium (Playwright, already
available locally) driven against the actual production static export served locally
(`python3 -m http.server` over `web/out/`), not `next dev`. Two checks per width×route combination
(42 total): (1) a programmatic overflow check — `document.documentElement.scrollWidth` compared
against `clientWidth` at each width, and (2) a full-page screenshot, visually reviewed.

**Issues found/fixed:** the automated overflow check found zero horizontal overflow at any of the 42
combinations, both before and after the fix below. Visual review of the screenshots found one real
defect: `/app/settings`'s platform-connection row (`Card` with `flex items-center justify-between`)
held its status `Badge` and "Connect" `Button` on the same fixed horizontal line as the platform
name, which wrapped the badge text onto two lines and crowded the row at 320-375px; it also showed
"Not connected" twice (once as a plain-text account-label fallback, once as the badge itself). Fixed
by changing the row to `flex-col gap-3 sm:flex-row sm:items-center sm:justify-between` (the same
responsive-stacking pattern `PageHeader` already used) and removing the redundant label — re-screenshotted
at 320/375px to confirm the fix, re-ran the overflow check to confirm no regression. No other
responsive defect was found: mobile nav remains fully usable and doesn't overlap content at any
tested width; desktop side nav appears correctly at exactly the 768px breakpoint; long
labels/descriptions wrap correctly without breaking any card/section layout; page headers stay
readable at all widths (`PageHeader` already stacks title+action below `sm:`); the public marketing
site (including its own pre-existing mobile hamburger menu) renders correctly at every tested width,
confirming the `(marketing)` route-group move didn't change its behavior.

## Design System

- **Tokens:** extended (not replaced) `app/globals.css`'s existing `:root`/`@theme inline` block —
  added `--color-status-{pending,progress,success,danger}[-soft]` (backing `lib/status.ts`'s tone
  mapping) and `--width-app-content` (42rem, narrower than the marketing site's own `max-w-6xl`).
- **Primitives:** six new `components/ui/` primitives — `Card`, `Badge`, `PageHeader`, `Spinner`,
  `EmptyState`, `ErrorState` — deliberately a small set matching what this milestone's pages
  actually need, not a speculative design system. `Button`/`Container` already existed from the
  marketing site and were reused as-is, unmodified.
- **Accessibility baseline:** semantic headings (`PageHeader` renders a real `<h1>`); `nav` elements
  carry `aria-label`; the active nav item carries `aria-current="page"`; `Spinner` uses
  `role="status"` with a real (not only visual) label; `ErrorState` uses `role="alert"`; the
  marketing site's mobile-menu toggle button carries `aria-expanded`/`aria-label` (pre-existing,
  reused, verified still correct after the route-group move). No `<input>`/`<label>` pairs exist yet
  in this shell — there are no real forms (upload, connect, schedule-edit) to label, since those
  features are explicitly out of this milestone's scope; the one interactive form-adjacent control
  that exists (the mobile menu toggle) is labeled. This is recorded honestly as a gap to close once
  a milestone actually introduces a form, not fabricated coverage for something that doesn't exist.

## Product Routes

- **`/app`:** Home/dashboard — `PageHeader` + three `QuickLinkCard`s into Library/Queue/Settings.
  Real navigation, no fake activity feed/stats (would require data this milestone deliberately
  doesn't wire up).
- **`/app/library`:** real empty state ("No videos yet") by default — batch upload is Milestone
  3.7's scope; the empty-state action is an inert placeholder signaling where upload will live.
- **`/app/queue`:** real empty state ("Nothing scheduled yet") by default — `items: QueueItem[] = []`
  hardcoded; `QueueItemCard`'s populated layout is real and tested directly
  (`tests/components/QueueItemCard.test.tsx`) even though the live page ships empty.
- **`/app/settings`:** Account section reflects the real session boundary (`useSession()`);
  Connected-platforms section shows connection *status* only from `lib/api/mockData.ts` — TikTok
  OAuth itself is Milestone 3.6's scope; the "Connect" action is inert by design.

## API Boundary

- **Client location:** `lib/api/client.ts` — a single `apiRequest<T>()`, the only place any future
  component should call a backend endpoint.
- **Error handling:** every failure mode (network failure, non-2xx response with or without a JSON
  body, malformed successful response) normalizes into a stable `ApiError` (`status`, `reasonCode` —
  `NETWORK_ERROR`/`HTTP_ERROR`/`MALFORMED_RESPONSE`), mirroring the backend's own error-shape
  convention. Verified directly by `tests/lib/client.test.ts` against a stubbed global `fetch`
  (no backend exists to test against for real).
- **Type strategy:** `lib/api/types.ts` defines frontend/product domain types
  (`VideoSummary`, `PlatformPostSummary`, `QueueItem`, `PlatformConnectionSummary`), deliberately
  looser than the backend's actual database columns — the UI never imports or assumes a
  Postgres/SQLite schema shape.

## Auth Preparation

- **Session abstraction:** `lib/session.tsx`'s `useSession()`/`SessionProvider` — the one seam every
  `/app/*` page/component depends on.
- **Mock/dev identity:** `DEV_MOCK_USER` (`id: "dev-local-user"`, `email:
  "local@pickle-batch.local"`) — loudly marked as development-only in the module's own top comment.
- **Fail-closed production behavior:** `SessionProvider` throws in a production build
  (`NODE_ENV === "production"`) unless `NEXT_PUBLIC_ALLOW_MOCK_SESSION === "true"` is explicitly
  set. Proven directly, not just asserted, by `tests/lib/session.test.tsx` (3 cases: renders outside
  production; throws in production without the flag; renders in production with the flag
  explicitly set).
- **Temporary Netlify flag:** `netlify.toml` sets `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` — a
  deliberate, documented, temporary shell-development configuration (this milestone has no real
  backend and therefore no real user-owned data to protect), not a shipped product decision, and
  not itself a secret (it's `NEXT_PUBLIC_`, browser-visible by design).
- **Blocker before real user data:** recorded verbatim, in three places (`lib/session.tsx`,
  `netlify.toml`, `docs/decisions/0010-frontend-app-shell.md`'s "Authentication Boundary") —
  **BLOCKER BEFORE REAL USER DATA ACCESS.** Before `/app` may read or mutate real user-owned data or
  call an authenticated API: (1) implement real server-verifiable authentication; (2) replace/remove
  the mock `SessionProvider` path; (3) remove `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` from
  `netlify.toml`; (4) verify production fails closed without a valid authenticated session; (5)
  never trust a client-supplied `user_id` as the authenticated identity.
- **Real provider selected:** no — explicitly out of scope for this milestone.

## Security

Reviewed via `git grep`/`grep` across `web/` (see command output retained in this milestone's working
notes; summarized here):

- `.env.local` — gitignored (`.env*` in `web/.gitignore`); confirmed absent from `git status`/
  `git ls-files`.
- `.env.example` — newly created, documents `NEXT_PUBLIC_API_BASE_URL`/
  `NEXT_PUBLIC_ALLOW_MOCK_SESSION` with a comment that anything `NEXT_PUBLIC_`-prefixed is bundled
  into the browser and must be treated as public. Found and fixed a real gap while adding it:
  `web/.gitignore`'s blanket `.env*` rule was also silently swallowing `.env.example` itself (a
  template with no real secret in it, meant to be committed); added a `!.env.example` negation,
  matching the root repository's own `.env.example` convention.
- `DATABASE_URL`/`SERVICE_ROLE`/`SECRET`/`PRIVATE_KEY`/`ACCESS_TOKEN`/`REFRESH_TOKEN` — grepped
  across `web/app`, `web/components`, `web/lib`, `web/tests`, and root-level `web/*.ts(x)`: every
  match is a documentation comment explicitly warning against ever doing this
  (`lib/api/client.ts`'s own module comment, `.env.example`'s comment) — no actual usage anywhere.
- Direct server-env references in client source: proven absent by a standing automated test
  (`tests/lib/no-secrets-in-client-bundle.test.ts`), not only a one-time grep — see "Testing" below
  for exactly what that test does and does not prove.
- Backend credentials referenced client-side: none. No `SERVICE_ROLE_KEY`, no Postgres connection
  string, no TikTok client secret exists anywhere in `web/`.
- `NEXT_PUBLIC_ALLOW_MOCK_SESSION` is treated as public/non-secret throughout — never logged as if
  sensitive, documented openly in `.env.example`/`netlify.toml`/this record.
- Mock user identity is not used as a trusted production identity anywhere — `DEV_MOCK_USER` only
  ever populates UI display (name/email in `AppHeader`/Settings); nothing in `lib/api/client.ts`
  attaches it to any request (there are no real requests yet), and the "BLOCKER" list above
  explicitly forbids trusting a client-supplied id once a real backend exists.

## Public Site

- **`/`:** renders correctly at all 6 validated widths; content/CTA/sections unchanged in substance
  from before the route-group move — confirmed via direct route-content check (`grep` for
  "Pickle Batch" in the served HTML) and visual screenshot review.
- **`/privacy`:** renders correctly; legal-content typography (`legal-content` CSS class, unchanged)
  reads correctly at 320px (verified via screenshot — long words/links wrap correctly, no overflow).
- **`/terms`:** renders correctly (same verification as `/privacy`).

## Deep Links

- **Direct route loading:** verified against the real static export (`web/out/`, the same directory
  Netlify would publish) served locally — every one of the 7 real routes returns HTTP 200 with its
  real, expected content at its canonical trailing-slash URL (`/app/library/`, not just `/app/library`).
- **Refresh behavior:** a direct load of a deep URL (no navigation from `/`) is exactly what static
  per-route `index.html` files support — confirmed by inspecting `web/out/`'s actual directory
  structure (`out/app/library/index.html` etc. exist as real files, not only reachable via
  client-side routing), and confirmed by curl'ing each route directly rather than navigating through
  the app.
- **Deployment configuration:** `next.config.ts`'s `trailingSlash: true` is what produces this
  per-route `index.html` shape; a bare path (no trailing slash) correctly 301-redirects to the
  canonical trailing-slash URL; a genuinely unknown path returns a non-200 status, and
  `web/out/404.html` exists at the root-level path Netlify's static-hosting convention expects to
  pick it up automatically (not independently verified against Netlify itself — see "Deferred").

## Testing

- **Frontend framework:** none existed before this milestone. Set up Vitest 5 + React Testing
  Library + `@testing-library/jest-dom` + `@testing-library/user-event`, jsdom environment
  (`web/vitest.config.mts`, `web/vitest.setup.ts`). Bumped `@types/node` from `^20` to `^22` to
  resolve a real peer-dependency conflict with Vitest 5 (Node 22 already matches
  `netlify.toml`'s own `NODE_VERSION`, so this aligns the dev-dependency typings with the actual
  deployment target rather than introducing a mismatch).
- **Frontend tests added:** 46, across 9 files —
  `tests/routes/marketing.test.tsx` (public routes + `SiteHeader`/`SiteFooter`),
  `tests/routes/app-routes.test.tsx` (all four `/app/*` pages),
  `tests/components/AppNav.test.tsx` (destination list, active-state prefix matching, nested-route
  handling, mobile/desktop parity),
  `tests/components/ui-primitives.test.tsx` (`Button`/`Card`/`Badge`/`PageHeader`/`Spinner`/
  `EmptyState`/`ErrorState`),
  `tests/components/QueueItemCard.test.tsx`,
  `tests/lib/client.test.ts` (`apiRequest()` error normalization — 5 cases),
  `tests/lib/status.test.ts` (status presentation mapping, `formatScheduledAt`),
  `tests/lib/session.test.tsx` (mock-session fail-closed behavior — 3 cases),
  `tests/lib/no-secrets-in-client-bundle.test.ts` (client-bundle env-var guardrail — 2 cases).
- **Frontend result:** 46 passed, 0 failed (`npm run test`).
- **Backend result:** 696 passed, unchanged from the Milestone 3.4 baseline (`.venv/bin/python3 -m
  pytest -q`, re-run after all frontend work completed) — confirms zero backend regression, as
  expected from a milestone that touched only `web/`, which shares no code with the Python backend
  in either direction.

## Build

- **Lint:** `npm run lint` (ESLint) — clean, zero warnings/errors.
- **Typecheck:** runs as part of `npm run build` (Next.js's own TypeScript pass) — clean.
- **Production build:** `npm run build` (static export, `output: "export"`) — succeeds. One real
  build-time issue found and fixed during this milestone: a stale `.next` type-validator cache
  referencing pre-route-group-move file paths (`app/page.tsx` etc.) caused a spurious
  `Cannot find module` error; resolved by clearing `.next` — not a product defect, a cache artifact
  of the route restructuring.
- **Routes emitted:** all 8 — `/`, `/privacy/`, `/terms/`, `/app/`, `/app/library/`, `/app/queue/`,
  `/app/settings/`, plus `/_not-found`.
- **Netlify validation:** not validated against the real Netlify service (no deploy was triggered —
  out of scope for local verification); validated locally against the exact artifact Netlify would
  publish (`web/out/`, built with `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` via a gitignored
  `.env.local`, matching what `netlify.toml`'s `[build.environment]` now sets) — see "Deep Links"
  above for what was and wasn't checked this way.

## Documentation

- Architecture: `docs/architecture/hosted-product-boundary.md` — new §18 (Frontend Application
  Boundary), update note at the top of the file, §14's frontend-hosting row updated.
- Evaluation: this record.
- ADR: `docs/decisions/0010-frontend-app-shell.md` (new — route structure, public/product boundary,
  mobile-first layout strategy, API-client boundary, auth/session boundary, the temporary
  mock-session deployment decision and its blocker, future native-compatibility assumptions).
- `AGENTS.md`: "Current Product Stage" updated (3.5 complete, 3.6+ next), Testing Requirements
  gained a frontend baseline line, Scope Guardrails gained a `/app`-specific guardrail, "Where to
  Find More" gained an ADR-0010 pointer.
- `CHANGELOG.md`: new dated entry.
- `README.md` (root): "Public Website" section renamed/expanded to "Frontend (Public Website +
  Product App Shell)".
- `web/README.md`: expanded — routes table now includes the four product routes, new "Auth /
  Session" and "API boundary" sections, structure diagram updated.
- `web/.env.example`: new.

## Deferred

- **Real auth:** not implemented — mock session only, explicitly fails closed in production without
  an opt-in flag. See `docs/decisions/0010-frontend-app-shell.md`'s blocker checklist.
- **TikTok connection UI:** Milestone 3.6's scope — Settings shows connection status only, "Connect"
  is inert.
- **Batch upload:** Milestone 3.7's scope — Library's empty-state action is inert.
- **Cadence/scheduling controls:** not built — Queue only displays, never edits.
- **Real queue data:** not wired — `/app/queue` ships with a hardcoded empty array; no backend call
  exists to replace it with yet.
- **Hosted workers:** not applicable to this milestone (frontend-only).
- **Native app:** not built or planned by this milestone; `lib/api/types.ts`/`client.ts`/`status.ts`
  were kept framework-agnostic (no Next.js/DOM API in their public surface) so a future native client
  could reuse the same request/error/type/presentation shapes against the same eventual backend.
- **Real Netlify deploy verification:** local validation only (static export served locally, matching
  the exact artifact and build-environment flag Netlify would use) — no live Netlify deploy was
  triggered as part of this milestone's verification.

## Guardrails Respected

No real auth provider integration, no TikTok connection UI, no batch upload, no real scheduling
controls, no real queue/calendar backend data, no hosted workers, no billing/analytics/native app.
The existing public marketing site and its Netlify deployment configuration were preserved (route
URLs unchanged, only their route-group ownership moved). No backend code was touched — confirmed by
the unchanged 696-test backend suite result.

## Required Final Report

**Milestone 3.5 — Mobile-First Responsive Web App Shell**

**Existing frontend**
- Framework before: Next.js 16 (App Router, TypeScript, Tailwind CSS v4), static export, public
  marketing site only.
- Important existing routes: `/`, `/privacy`, `/terms`.
- Migration performed: moved those three routes into an `app/(marketing)/` route group via `git mv`
  (URLs unchanged); no other migration was needed.

**Frontend architecture**
- Framework: unchanged (Next.js 16, App Router, TypeScript, Tailwind CSS v4, static export).
- Routing: two route groups — `app/(marketing)/` (public) and `app/app/` (product), each with its
  own nested layout; the true root layout owns only html/body/font/metadata.
- Public/product boundary: structural, not conventional — separate route subtrees, separate layouts,
  separate chrome; only `app/app/*` is wrapped in `SessionProvider`.

**App shell**
- Mobile navigation: `AppBottomNav` — fixed bottom tab bar, 4 destinations, safe-area padding,
  `min-h-14` touch targets.
- Desktop navigation: `AppSideNav` — left sidebar, same nav-item list/active-state logic as mobile.
- Content layout: `max-w-5xl` container, `pb-20` mobile bottom padding to clear the fixed nav.

**Responsive behavior**
- Breakpoints: Tailwind `sm` (640px) / `md` (768px).
- Widths manually validated: 320, 375, 390, 768, 1024, 1440px, across all 7 routes (42 combinations),
  via real headless Chromium against the actual static export.
- Issues found/fixed: one — `/app/settings`'s platform-connection row wrapped/crowded at 320-375px
  and duplicated "Not connected"; fixed by responsive stacking and removing the duplicate label.
  Re-validated after the fix.

**Design system**
- Tokens: extended the existing palette with status-color tokens and one app-content-width token.
- Primitives: 6 new (`Card`, `Badge`, `PageHeader`, `Spinner`, `EmptyState`, `ErrorState`); 2 reused
  unmodified (`Button`, `Container`).
- Accessibility baseline: semantic headings, labeled/`aria-current`-marked nav, `role="status"`/
  `role="alert"` on loading/error primitives. No `<label>`/`<input>` pairs exist yet — no real form
  exists in this milestone's scope to attach one to; recorded as a gap, not fabricated.

**Product routes**
- `/app`: real dashboard with links to the other three sections.
- `/app/library`: real empty state by default ("No videos yet"); upload action inert (Milestone 3.7).
- `/app/queue`: real empty state by default ("Nothing scheduled yet"); populated layout
  (`QueueItemCard`) built and tested but not fed real data.
- `/app/settings`: real session-backed account section; platform-connection status display only,
  "Connect" inert (Milestone 3.6).

**API boundary**
- Client location: `web/lib/api/client.ts` (`apiRequest()`), one seam, nothing calls it yet.
- Error handling: every fetch failure mode normalizes into a stable `ApiError` (`status`,
  `reasonCode`), tested directly against a stubbed `fetch`.
- Type strategy: `web/lib/api/types.ts` — frontend/product domain types, decoupled from the backend's
  actual database columns.

**Auth preparation**
- Session abstraction: `web/lib/session.tsx`'s `useSession()`/`SessionProvider`.
- Mock/dev identity: `DEV_MOCK_USER`, loudly marked development-only.
- Fail-closed production behavior: yes — proven by 3 direct tests (`tests/lib/session.test.tsx`).
- Temporary Netlify flag: `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true`, set deliberately in `netlify.toml`,
  documented as temporary and non-secret.
- Blocker before real user data: recorded verbatim in `lib/session.tsx`, `netlify.toml`, and
  ADR-0010 — real auth, remove mock path, remove Netlify flag, re-verify fail-closed, never trust a
  client-supplied `user_id`.
- Real provider selected: no.

**Security**
- `.env.local`: gitignored, confirmed absent from `git status`/`git ls-files`.
- `.env.example`: tracked (fixed a pre-existing `.env*` gitignore rule that was also swallowing it).
- Direct server-env references in client source: none found; a standing automated test enforces this
  going forward (proves absence of the *specific* mistake it targets, not a general secret-leak
  guarantee — stated precisely, not overclaimed, in ADR-0010).
- Secrets exposed: none.
- Backend credentials referenced client-side: none.

**Public site**
- `/`: unchanged in substance, verified at all 6 widths, content confirmed present after the
  route-group move.
- `/privacy`: unchanged, verified at all 6 widths including narrow-width legal-text readability.
- `/terms`: unchanged, same verification as `/privacy`.

**Deep links**
- Direct route loading: verified against the real static export — every route 200s with real content
  at its canonical URL.
- Refresh behavior: supported by design (`trailingSlash: true` produces real per-route
  `index.html` files, confirmed present on disk).
- Deployment configuration: bare paths 301-redirect to canonical URLs; unknown paths return non-200;
  `out/404.html` exists at the path Netlify's convention expects (not independently verified against
  live Netlify).

**Testing**
- Frontend framework: Vitest + React Testing Library (newly set up this milestone).
- Frontend tests added: 46, across 9 files.
- Frontend result: 46 passed, 0 failed.
- Backend result: 696 passed, unchanged from the Milestone 3.4 baseline.

**Build**
- Lint: clean.
- Typecheck: clean (via `npm run build`).
- Production build: succeeds; one stale-cache issue found and fixed (not a product defect).
- Routes emitted: all 8 (`/`, `/privacy/`, `/terms/`, `/app/`, `/app/library/`, `/app/queue/`,
  `/app/settings/`, `/_not-found`).
- Netlify validation: validated locally against the exact static-export artifact and build-time flag
  Netlify's config now sets; no live Netlify deploy was triggered.

**Documentation**
- Architecture: `docs/architecture/hosted-product-boundary.md` updated (new §18, top-of-file note,
  §14 row).
- Evaluation: this record.
- ADR: `docs/decisions/0010-frontend-app-shell.md` (new).
- AGENTS: updated (stage, testing baseline, scope guardrail, pointer).
- CHANGELOG: updated (new dated entry).

**Deferred**
- Real auth: not built.
- TikTok connection UI: not built (Milestone 3.6).
- Batch upload: not built (Milestone 3.7).
- Cadence/scheduling: not built.
- Real queue data: not wired.
- Hosted workers: not applicable.
- Native app: not built; boundaries kept reusable for one.

**Overall**

**COMPLETE.**
