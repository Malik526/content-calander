/**
 * queryClient.ts — the app's one TanStack Query client configuration
 * (Milestone 3.15). Per-resource stale times live in the hooks/ that own
 * each resource; these are the defaults they override.
 *
 * - Cached data renders immediately on revisit; a stale query refetches in
 *   the background and the UI updates only if the data changed
 *   (structural sharing).
 * - gcTime keeps unused data for 30 minutes so leaving a screen and coming
 *   back within a session is instant.
 * - Refetch on window focus and reconnect, so a tab left open picks up
 *   worker-driven publishing changes when the user returns.
 * - Retries skip 4xx errors: a 401/403/404/409 won't fix itself.
 */

import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";

export const DEFAULT_STALE_TIME_MS = 30_000;

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status !== null && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 2;
}

export function createAppQueryClient({ retry = true }: { retry?: boolean } = {}): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_STALE_TIME_MS,
        gcTime: 30 * 60_000,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        retry: retry ? shouldRetry : false,
      },
      mutations: { retry: false },
    },
  });
}
