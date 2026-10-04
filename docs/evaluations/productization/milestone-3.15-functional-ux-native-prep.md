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

## Tap targets (fixed in the 3.15 follow-up)

The first audit found many controls under 44 px at phone width: text-style Delete/Remove links
(16 px), filter tabs (32 px), header links (20–24 px), Queue toggles and pickers (30–34 px),
checkboxes, and buttons at 38–42 px. Fixed without changing their look:

- **`tap-target`** (`web/app/globals.css`): on touch screens only (`pointer: coarse`), an
  invisible centered `::after` hit box of at least 44×44 px. Added to the `Button` and
  `ErrorState` primitives and to every small link/button in the `/app` screens. Checkboxes get it
  through their wrapping `<label>`.
- **Native controls** can't carry `::after`, so the timezone select, time picker, slot picker and
  file input use `pointer-coarse:min-h-11`. That's 44 px tall on touch screens, same styling.
  This is the only visible change, and only on touch devices.
- **`tap-target-dot`** for calendar slot dots: a full 44×44 px when a day has one slot. When it
  has several, each dot is 44 px tall but only as wide as its own column (14 px), so neighbours
  never overlap. Making those dots 44 px wide would need a visual or interaction change; List
  view remains the comfortable way to pick between same-day slots.

Verified in Chromium with touch emulation at 390 px, including the delete confirmation, time
picker and calendar/selected-slot states: every control has an effective hit area of at least
44×44 px except the multi-slot calendar dots above, and there's no horizontal overflow.
Screenshots with the hit boxes on and forced off are pixel-identical on all four routes. With a
mouse (`pointer: fine`) nothing changes.

Still present: the first visit to a resource shows a placeholder (no data yet), and a hard reload
starts with "Loading your account…" while the stored session is read.

## Not verified

- An authenticated walk against the real backend in a logged-in browser. Google sign-in can't be
  automated here; the harness used a seeded session and fixture API. The real API contracts are
  unchanged, and backend tests weren't affected (no backend code changed).
