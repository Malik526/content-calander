"use client";

/**
 * QueryProvider — owns the app's one TanStack Query client for the whole
 * /app shell (Milestone 3.15). It lives in app/app/layout.tsx, which Next
 * keeps mounted across /app/* navigations, so server state fetched on one
 * screen is still there on the next.
 *
 * User scoping: query keys already start with the user id (lib/query/keys.ts),
 * and when the signed-in user changes (sign-out, or a different account
 * signing in) everything cached for the previous account is removed, so
 * nothing from it stays in memory. Only the previous user's entries are
 * removed, not the whole cache: child screens subscribe (and start the new
 * user's fetches) before this provider's effect runs, and a full clear()
 * would throw those in-flight fetches away.
 *
 * Props: children; client (optional — tests pass their own client).
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import { createAppQueryClient } from "@/lib/query/queryClient";
import { useSession } from "@/lib/session";

export function QueryProvider({ children, client }: { children: ReactNode; client?: QueryClient }) {
  const [queryClient] = useState(() => client ?? createAppQueryClient());
  const { user } = useSession();
  const userId = user?.id ?? null;
  const previousUserId = useRef(userId);

  useEffect(() => {
    const previous = previousUserId.current;
    if (previous !== userId) {
      if (previous !== null) queryClient.removeQueries({ queryKey: queryKeys.user(previous) });
      queryClient.getMutationCache().clear();
      previousUserId.current = userId;
    }
  }, [userId, queryClient]);

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
