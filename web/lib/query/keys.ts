/**
 * keys.ts — TanStack Query keys for server state (Milestone 3.15).
 *
 * Every key starts with the signed-in user's id, so cached data from one
 * account can never answer a query made by another (QueryProvider also
 * clears the cache when the user changes). The access token is never part
 * of a key: it rotates on refresh, and a new token must not mean a cache
 * miss. See docs/decisions/0017-client-server-state-cache.md.
 */

export const queryKeys = {
  /** Everything cached for one user; invalidate this to refetch it all. */
  user: (userId: string) => ["user", userId] as const,
  videos: (userId: string) => ["user", userId, "videos"] as const,
  cadence: (userId: string) => ["user", userId, "cadence"] as const,
  /** All Queue windows; use for invalidation after any slot change. */
  queueSlotsAll: (userId: string) => ["user", userId, "queue-slots"] as const,
  queueSlots: (userId: string, from: string, to: string) => ["user", userId, "queue-slots", from, to] as const,
  tiktokConnection: (userId: string) => ["user", userId, "platforms", "tiktok"] as const,
};
