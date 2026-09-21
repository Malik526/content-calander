# ADR-0010: Frontend App Shell

## Status

Accepted

## Context

Through Milestone 3.4, `web/` was a public marketing site only (`/`, `/privacy`, `/terms` — Milestone
2.0.1) with no authenticated product surface at all, while the backend (Milestones 3.0-3.4) built out
a real hosted-product boundary: ownership, Postgres, and object storage all exist and are tested, but
nothing on the frontend could show any of it. Milestone 3.5's job was to build the mobile-first,
reusable **app shell** the eventual product UI (Milestones 3.6+: TikTok connection, batch upload,
cadence/scheduling controls, a real queue/calendar) will be built inside — not any of those features
themselves, and explicitly not real authentication. This is a genuinely durable set of architecture
decisions (route structure, the public/product boundary, the API-client and session boundaries), which
is why it gets an ADR rather than only an evaluation record — the same bar ADR-0007/0008/0009 applied
on the backend side.

This had to happen without breaking the existing public site or its Netlify deployment, without
building real auth, TikTok connection UI, batch upload, real scheduling controls, real queue/calendar
data, hosted workers, billing, analytics, or a native app — all explicitly out of scope per the
brief's own guardrails.

## Decision

### Framework and route structure: extend the existing Next.js app, not a new project

`web/` was already Next.js 16 (App Router, TypeScript, Tailwind CSS v4, static export via
`output: "export"`) from Milestone 2.0.1 — no framework change. Two Next.js **route groups** split the
app in two without affecting any URL (a route group's parenthesized folder name is stripped from the
path): `app/(marketing)/` (`/`, `/privacy`, `/terms` — unchanged URLs, moved via `git mv` so their
git history is preserved) and `app/app/` (`/app`, `/app/library`, `/app/queue`, `/app/settings` — new).
`app/layout.tsx` (the true root layout) was cut down to only what is genuinely global — `<html>`/
`<body>`, font, and metadata defaults — with each route group owning its own chrome via its own nested
layout (`app/(marketing)/layout.tsx` renders `SiteHeader`/`SiteFooter`; `app/app/layout.tsx` renders
the product shell's header/nav). This means the two experiences can diverge completely (different
header, different nav model, different session requirements) without either one fighting the root
layout's assumptions, and without a shared layout accidentally leaking one section's chrome into the
other.

### Public/product route boundary: structural, not just visual

The marketing site and the product shell share no layout, no header/footer component, and no session
requirement — `(marketing)` routes render with zero dependency on `lib/session.tsx`, and remain fully
public and pre-renderable exactly as before. `app/app/*` routes are the only ones wrapped in
`SessionProvider`. This is deliberately a structural boundary (different route subtree, different
layout ownership) rather than a convention someone could accidentally violate by importing the wrong
component into the wrong page.

### Mobile-first layout strategy: one nav model, two renderings

`components/app/AppNav.tsx` defines a single `navItems` list (Home, Library, Queue, Settings — matching
the near-term roadmap, not a speculative larger set) and a single `isActive()` prefix-match helper, then
renders it twice: `AppBottomNav` (a fixed bottom tab bar, `md:hidden`, `env(safe-area-inset-bottom)`
padding for iOS home-indicator clearance — the primary navigation on mobile, reachable one-handed) and
`AppSideNav` (a left sidebar, `hidden md:block` — the desktop enhancement of the exact same model, not
a separate app). This mirrors the marketing site's own established pattern (`SiteHeader`'s mobile menu
toggle vs. its desktop nav row) rather than inventing a new one. `app/app/layout.tsx` gives its content
column bottom padding on mobile (`pb-20`) specifically so the fixed bottom nav never overlaps the last
visible content — a real, testable layout concern, not a cosmetic afterthought.

**Manual + automated responsive validation performed** (see the Milestone 3.5 evaluation record for
full detail): a real Chromium instance (Playwright, already cached locally) loaded every public and
product route at six widths (320/375/390/768/1024/1440px) against the actual static export served
locally, and a programmatic check confirmed `document.documentElement.scrollWidth` never exceeded the
viewport width at any of the 42 width×route combinations — zero horizontal overflow, not merely
asserted from reading Tailwind classes. This pass found one real defect, fixed before this ADR was
written: `/app/settings`'s platform-connection row (`app/app/settings/page.tsx`) held its status badge
and "Connect" button on a fixed horizontal row that wrapped awkwardly at 320-375px, and separately
duplicated "Not connected" as both a plain-text account label and the status badge. Fixed by stacking
that row (`flex-col` below `sm:`, `flex-row` at `sm:` and up — the same responsive-stacking pattern
`PageHeader` already used) and removing the redundant label instead of showing it twice.

### API-client boundary: one function, no backend to call yet

`lib/api/client.ts` exports a single `apiRequest<T>()` — every future component-level fetch is meant to
go through it, never a direct `fetch()` call, so the base URL, header handling, and error normalization
all live in exactly one place. It normalizes every failure mode (network failure, non-2xx response,
malformed JSON) into a stable `ApiError` (`status`, `reasonCode` — `NETWORK_ERROR` / `HTTP_ERROR` /
`MALFORMED_RESPONSE`) rather than letting a raw `fetch`/`Response`/`SyntaxError` leak into UI code,
mirroring the backend's own `PublishError`/storage-error shape so error handling reads the same way on
both sides of the stack. No FastAPI backend exists yet (see `docs/architecture/hosted-product-boundary.md`
§12) — nothing in this shell actually calls `apiRequest()` today; every product page reads from
`lib/api/mockData.ts` instead, or ships genuinely empty (`/app/library`, `/app/queue` render their real
"nothing yet" empty state by default, not a permanently-populated demo). This keeps the eventual swap-in
a one-file change (point real pages at `apiRequest()` instead of `mockData.ts`) rather than an
application-wide rewrite.

**Backend API boundary alignment:** this client assumes `browser -> API -> Postgres/storage`, never
`browser -> Supabase directly` — consistent with `hosted-product-boundary.md`'s five-layer model and
with ADR-0008/0009's own choice to keep Postgres/Storage credentials server-side only. `NEXT_PUBLIC_
API_BASE_URL` is the only backend-related value this module ever reads; nothing server-only has any
reason to exist in `web/` at all.

### Shared types: frontend domain types, not raw database rows

`lib/api/types.ts` defines `VideoSummary`, `PlatformPostSummary`, `QueueItem`, `PlatformConnectionSummary`
— UI/product concepts, deliberately looser than `videos`/`platform_posts`' actual Postgres/SQLite
columns (e.g. `PlatformPostStatus` carries only the four states the UI distinguishes, not
`retry_count`/`next_status_check_at`/every internal column). The UI never imports or assumes a database
schema shape directly. `lib/status.ts` is the one place a raw backend status enum (`PENDING`,
`PUBLISHING`, `PUBLISHED`, `FAILED`) gets turned into a label/color a person reads (`Badge`'s `tone`
prop) — no page/component computes its own label or duplicates this mapping.

### Authentication Boundary

No real authentication provider was selected or integrated this milestone (explicit guardrail) — see
"Deferred" below. `lib/session.tsx` is the one seam every product page/component depends on
(`useSession()`), never a hardcoded mock user imported directly and never a real provider's SDK
anywhere else — swapping in real authentication later means rewriting `SessionProvider`'s internals
only, with every consumer of `useSession()` unaffected.

`DEV_MOCK_USER` is an explicit, loudly-marked development-only stand-in. `SessionProvider` **fails
closed by default**: in a production build (`NODE_ENV === "production"`), it throws unless
`NEXT_PUBLIC_ALLOW_MOCK_SESSION === "true"` is explicitly set — proven directly, not just asserted, by
`tests/lib/session.test.tsx` (mock renders outside production; throws in production without the flag;
renders with the flag explicitly set).

**Temporary Netlify deployment decision:** the actual deployed build currently sets
`NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` in `netlify.toml`, because Milestone 3.5 ships a shell with no
real backend and therefore no real user-owned data to protect — the alternative (a production build
that always fails) would make this milestone's own required deliverable (a working Netlify deployment
of the shell) impossible. This is a temporary shell-development configuration, not a shipped product
decision, and it is not itself a secret — it is `NEXT_PUBLIC_` and browser-visible by design.

**BLOCKER BEFORE REAL USER DATA ACCESS.** Before `/app` may read or mutate any real user-owned data or
call an authenticated API:

1. Implement real, server-verifiable authentication.
2. Replace/remove the mock `SessionProvider` path in `lib/session.tsx`.
3. Remove `NEXT_PUBLIC_ALLOW_MOCK_SESSION=true` from `netlify.toml`, so the production guard reverts to
   failing closed by default.
4. Verify the production build fails closed without a valid authenticated session (re-run
   `tests/lib/session.test.tsx`'s production-without-flag case against the real change).
5. Never trust a client-supplied `user_id` as the authenticated identity — the backend must derive the
   identity from a verified session/token server-side, exactly as `hosted-product-boundary.md` §5's
   multi-tenant execution invariant already requires for background jobs; a mock or client-asserted id
   is not a substitute for that.

This same invariant is restated at the point of actual risk — `netlify.toml`'s own comment next to the
flag, and `lib/session.tsx`'s module comment — so it is visible to whoever next touches either file,
not only recorded here.

**Precision on the client-source env-var test's actual guarantee:** `tests/lib/no-secrets-in-client-
bundle.test.ts` statically scans `app/`, `components/`, and `lib/` for any `process.env.X` reference
that isn't `NEXT_PUBLIC_`-prefixed (or `NODE_ENV`), and fails if one exists. This proves that today's
source code contains no *direct* reference to a server-only environment variable — it does not, and
cannot, prove that no secret could ever leak into the client bundle by some other path (a
dynamically-constructed property access, a secret embedded in fetched data and then logged, a future
dependency that reads `process.env` internally and re-exports something server-only, etc.). It is one
real, automated guardrail against the specific mistake it targets, not a general secret-leak proof.

### PWA: light metadata only, not a real PWA

`public/manifest.webmanifest` (name, icons, `start_url: "/app"`, `display: "standalone"`) plus a
correct mobile `viewport` (`app/layout.tsx`) — no service worker, no offline logic, no install-prompt
handling. Icons (`public/icon-192.png`, `icon-512.png`, `public/apple-touch-icon.png`,
`app/favicon.ico`) are generated from the project's real app icon (`images/pickle-icon-no-bg.png`), not
a placeholder. This is the cheap, worthwhile subset per the brief's own guardrail ("light PWA metadata
... no service worker") — a real installable/offline PWA is a future decision, not made here.

### Design tokens and primitives: extend, don't replace

The marketing site's existing token system (`app/globals.css`'s `:root`/`@theme inline` block — colors,
one accent, borders) was extended, not replaced: new status-presentation tokens
(`--color-status-{pending,progress,success,danger}[-soft]`, backing `lib/status.ts`'s tone mapping) and
one new layout token (`--width-app-content`, narrower than the marketing site's own `max-w-6xl`, since
product screens read better closer to a document/list width). Six new primitives in `components/ui/`
(`Card`, `Badge`, `PageHeader`, `Spinner`, `EmptyState`, `ErrorState`) — deliberately a small set
matching what this milestone's pages actually need, not a speculative design system; `Button`/`Container`
already existed from the marketing site and were reused as-is.

### Future native compatibility assumption

No native app exists or is planned by this milestone, but the boundaries chosen keep that door open
without having built toward it prematurely: `lib/api/types.ts`/`lib/api/client.ts` are plain
TypeScript with no Next.js- or DOM-specific API in their public surface (a future React Native or other
client could reuse the same request/error/type shapes against the same eventual FastAPI backend), and
`lib/status.ts`'s presentation mapping is pure data, not JSX, so a native UI could reuse the same
label/tone decisions without reusing any web component.

## Consequences

- Every future product page should read through `lib/api/client.ts` (once a real backend exists) and
  `lib/status.ts` for any status display — introducing a second ad hoc fetch call or a second status-
  label mapping anywhere else would fork this boundary, not extend it.
- The single biggest open risk this ADR records is also its own explicit blocker: real authentication
  does not exist, and the deployed shell currently runs on an explicitly-flagged mock session. Any
  future milestone that adds real user-owned data access must close the "BLOCKER BEFORE REAL USER DATA
  ACCESS" list above before that data becomes reachable in production — this is not optional cleanup,
  it is the condition under which the current Netlify configuration is safe at all.
- `AppNav.tsx`'s four-item list is expected to grow as Milestones 3.6+ add real sections — the
  shared-list-rendered-twice pattern should be extended, not forked into two independently-maintained
  navs.
- `lib/api/mockData.ts` is explicitly not meant to become a permanent fallback the real client
  degrades to if a future API call fails — it exists only because no backend exists yet, and should be
  deleted (or reduced to Storybook/test-fixture use only) once real pages read from `apiRequest()`.
- The `(marketing)`/`app/app` route-group split, and root-layout-owns-nothing-but-global-chrome
  pattern, should be the template for any future top-level section this product adds (e.g. a future
  `/admin` or `/onboarding` area), rather than growing either existing route group to cover it.
