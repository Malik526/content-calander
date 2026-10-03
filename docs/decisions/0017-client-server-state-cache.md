# ADR-0017: Client Server-State Cache (TanStack Query) and UI-State Rule

## Status

Accepted (Milestone 3.15).

## Context

The `/app` shell (`web/`, a static-export Next.js client app) felt like separate pages rather
than one application. Moving between Home, Library, Queue and Settings showed a loading
placeholder on every visit and refetched everything. Measured in a real browser (fixture API with
400 ms latency, 390 px viewport, see the 3.15 evaluation): every navigation took about 810 ms and
showed a spinner, and one walk around the app made 27 API requests.

Root causes, from reading the code:

1. **No shared server state.** Each page and data-owning component (`app/app/page.tsx`,
   `library/page.tsx`, `settings/page.tsx`, `QueueScheduling`, `QueueBoard`) held fetched data
   in its own `useState` and fetched in a mount `useEffect`. The layout and its providers do stay
   mounted across `/app/*` navigation, but pages unmount, so their data was thrown away on every
   route change.
2. **Spinner-first rendering.** Each of those components started from `null` and rendered a
   full loading placeholder until its request returned, even when the same data had been loaded
   seconds earlier.
3. **Duplicate fetching of the same resource.** `GET /api/videos` was fetched separately by Home,
   Library and the Queue board. TikTok status was fetched by Home and Settings. Cadence was
   fetched by Home and the Queue.
4. **Manual cross-view refresh.** Consistency after mutations depended on ad hoc wiring: a
   `refreshKey` counter (cadence save → Queue), a "was uploading" ref (upload → Library), and
   whole-board reloads after Queue actions. Nothing refreshed the Library after a Queue action.
5. **Errors replaced content.** A failed refresh replaced the whole section with an error
   screen, and Home silently swallowed errors and showed quick links instead.
6. **Session context churn.** `SessionProvider` published a new context object on every Supabase
   auth event, including the identical `SIGNED_IN` Supabase re-emits when a tab regains focus,
   re-rendering the whole shell. Pages refetched whenever the token string changed.

Navigation itself was already correct: `next/link`, a persistent layout, no full page loads.
Auth resolution adds one spinner only on a hard load (reading the stored session), not during
navigation. Browser HTTP caching was not a factor: requests are client-side `fetch` calls with a
bearer token, and the API sends no cache headers.

## Decision

### 1. TanStack Query is the one server-state cache

`@tanstack/react-query` v5, one `QueryClient` per browser session, provided by
`lib/query/QueryProvider.tsx` inside the persistent `/app` layout (via
`components/app/AppDataProviders.tsx`). No custom caching framework, no Next.js server actions or
server components for data, so the same hooks/contracts can be reproduced in a React Native
client.

- **Keys** (`lib/query/keys.ts`) always start with `["user", userId, ...]`. The access token is
  never part of a key, so a token refresh does not cause a cache miss.
- **Hooks own resources** (`web/hooks/`): `useVideos`, `useCadence`, `useQueueSlots(from, to)`,
  `useTikTokConnection`. Components never call `lib/api/*` read functions directly for server
  state.
- **Defaults** (`lib/query/queryClient.ts`): 30 s stale time, 30 min garbage-collection time,
  refetch on window focus and reconnect, retries skip 4xx.

| Resource | Stale time | Notes |
|---|---|---|
| videos (Library/Home/Queue unscheduled list) | 30 s | Polls every 15 s while any video is `PUBLISHING` |
| queue slots (per month window) | 30 s | Keeps the previous month visible while a new month loads; polls every 15 s while a slot is `PUBLISHING` |
| cadence | 5 min | Changes only when this user saves it |
| TikTok connection | 5 min | Changes only through Connect (full-page OAuth round trip) or Disconnect |
| current user | n/a | Comes from the Supabase session; `GET /api/me` is not used by the UI |

Publishing state is never left permanently stale: it's refetched on revisit after 30 s, on window
focus, after every Queue mutation, and polled while in flight.

### 2. Mutations update or invalidate exactly what they affect

| Mutation | Cache effect |
|---|---|
| Upload (`lib/uploads.tsx`) | Invalidate videos (on success or failure, since a partial batch still created rows) |
| Delete video (`useVideoActions`) | Invalidate videos |
| Save cadence (`useSaveCadence`) | Write the saved cadence into the cache; invalidate all Queue windows |
| Assign / assign-next / remove / retry (`useQueueActions`) | Invalidate all Queue windows and videos (the slot and the video's `publish_status` move together) |
| Caption save/generate (`useQueueActions`) | Patch that video's caption inside cached Queue windows (no refetch, so other slots' drafts survive) |
| TikTok disconnect (`useTikTokActions`) | Write the returned status into the cache |
| TikTok connect | Full-page OAuth round trip, so the cache starts fresh on return |

### 3. Rendering rule: placeholders only when nothing is cached

A loading placeholder is shown only when a query has no data yet (first load). A background
refresh never replaces content. A failed background refresh keeps the last data and shows an
inline "Couldn't refresh … Retry" notice. A first-load failure shows an error with Retry.

### 4. Server state vs UI state

- **Server state** (videos, cadence, queue slots, platform connections, publish status, user)
  lives only in the query cache.
- **UI state** that would be annoying to lose on navigation lives in `lib/ui-state.tsx`
  (`usePersistentUiState(key, initial)`): an in-memory map for the session. It is used today for
  the Library filter, the Queue's list/calendar view and displayed month, and the unsaved cadence
  draft (cleared on save). It never touches browser storage.
- Everything else (open editors, busy flags, confirm prompts, picker draft time) stays component
  `useState`.

### 5. User isolation

Keys are user-scoped. When the session's user changes (sign-out or a different account),
`QueryProvider` removes the previous user's queries and the mutation cache, and
`AppDataProviders` remounts the UI-state and upload providers (keyed by user id), dropping drafts
and upload results. Only the previous user's entries are removed, not `clear()`: child screens
start the new user's fetches before the provider's effect runs, and a full clear discarded them
(a bug caught by the 3.15 tests).

### 6. Session stability

`SessionProvider` keeps the previous session object when an auth event changes nothing a consumer
reads (status, token, user id/email/name).

## Consequences

- Revisits render from cache in about 45 ms with no placeholder; one walk around the app makes 4
  requests instead of 27 (3.15 evaluation).
- Data can be up to 30 s old on revisit before the background refresh lands; it then updates in
  place. Acceptable because every write the user makes refreshes the affected entries
  immediately, and in-flight publishing is polled.
- A cadence change made elsewhere (another device) while this tab holds an unsaved draft is not
  merged into the draft; with no draft the form always shows the latest saved cadence.
- The enabled-but-empty-day state inside `WeeklyRhythmEditor` is component state and does not
  survive navigation (no data is lost; an empty enabled day saves nothing).
- New screens must follow the rule: read server state through a `hooks/` query hook, write
  through a mutation that updates/invalidates the affected keys, never fetch in a component
  effect.

## Alternatives considered

- **SWR**: comparable, but weaker mutation/invalidation primitives for the cross-resource updates
  above (Queue action → videos).
- **Hand-rolled context cache**: rejected by the brief and by maintenance cost (dedupe, stale
  times, focus refetch, polling, garbage collection).
- **Persisting the query cache to browser storage**: out of scope (offline-first is deferred) and
  would need care around user isolation.
