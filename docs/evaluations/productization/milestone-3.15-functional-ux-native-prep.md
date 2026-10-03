# Milestone 3.15 — Functional UX, Client State/Caching + Native Prep: Evaluation

Architecture: [ADR-0017](../../decisions/0017-client-server-state-cache.md). Native findings:
[native-ios-readiness.md](../../architecture/native-ios-readiness.md).

## Browser navigation measurements (2026-10-03)

Method: both builds were exported statically (`next build`, `output: "export"`) with
`NEXT_PUBLIC_API_BASE_URL` pointed at a fake origin and served locally. Playwright (Chromium)
seeded a fake Supabase session in browser storage and answered every API call from fixtures with
a **400 ms delay**. It walked the bottom nav (Home → Library → Queue → Settings → Library → Queue
→ Home, twice). For each navigation it recorded:

- time until content unique to that page was visible;
- whether a MutationObserver ever saw a loading placeholder;
- the API requests made.

"Before" is commit `094dc82` (end of 3.14); "after" is this milestone. Mobile is 390×844 with
touch; desktop is 1280×860.

| | Before (mobile) | After (mobile) | After (desktop) |
|---|---|---|---|
| First load (`/app/`) | ~950 ms, spinner | ~920 ms, spinner | same |
| First visit to Queue | ~810 ms, 2 spinners | ~830 ms, 1 spinner (slots only; videos/cadence already cached) | same |
| First visit to Library / Settings (after Home) | ~810 ms, spinner | **~45 ms, no placeholder, 0 requests** (shared cache) | same |
| Every revisit | ~810 ms, spinner, refetch | **40–57 ms, no placeholder, 0 requests** | same |
| Requests for one 13-step walk | 27 | **4** | 4 |

Additional browser checks on the after build (all passed):

- **Mutation propagation:** "Assign to next available slot" in Queue, then Library shows the video
  as Scheduled with no reload.
- **UI state:** the Library "Published" filter survives Library → Queue → Library.
- **Stale revisit:** after the server-side status changed and 31 s passed, revisiting Library
  rendered the cached list immediately (48 ms, no placeholder), made one background request, and
  updated the badge to Published.
- **Hard reload** of `/app/library/` refetches from the server and rebuilds correctly.
- **Sign out** lands on `/login/` with no prior-user content on screen.
- **Mobile layout:** no horizontal overflow on Home, Library, Queue or Settings at 390 px. Library
  overflowed by 66 px before this milestone (the four filter tabs from 3.14 couldn't wrap); fixed
  with `flex-wrap`.

## Automated coverage

`web/tests/routes/app-state.test.tsx` (13 tests) covers:

- navigation keeps data with one request in total;
- cached data shows during an in-flight refresh;
- one request per resource under StrictMode with several screens mounted;
- a failed background refresh keeps data with Retry;
- a first-load error shows Retry;
- assign → Library status, cadence save → Queue refresh and Home from cache, TikTok disconnect →
  Home, delete → Queue list;
- Library filter, cadence draft and Queue view survive navigation;
- an account switch shows none of the previous user's data or UI state and removes their cache;
- sign-out empties the cache.

`tests/lib/session.test.tsx` covers the session-object stability fix (verified failing without
it). Existing page tests run inside the real provider stack via `tests/test-utils.tsx`.

## Remaining UX issues (deferred to the visual redesign)

- Several tap targets are under 44 px at phone width: text-style Delete/Remove links (16 px),
  filter tabs (32 px), header Sign out (24 px), Queue view toggles and slot pickers (30–34 px),
  native checkboxes. The bottom nav is fine. These are styling decisions for the planned
  design-system pass, not state bugs.
- The first visit to a resource still shows a placeholder (no data to show yet), and a hard reload
  starts with "Loading your account…" while the stored session is read.

## Not verified

- An authenticated walk against the real backend in a logged-in browser. Google sign-in can't be
  automated here; the harness used a seeded session and fixture API. The real API contracts are
  unchanged, and backend tests weren't affected (no backend code changed).
