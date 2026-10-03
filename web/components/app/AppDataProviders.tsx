"use client";

/**
 * AppDataProviders — the client-state stack for the /app shell
 * (Milestone 3.15), shared by app/app/layout.tsx and the tests:
 *
 *   QueryProvider    server state (TanStack Query), cleared on user change
 *   UiStateProvider  in-memory UI state that survives navigation
 *   UploadProvider   in-progress batch upload feedback
 *
 * The last two are keyed by the signed-in user id, so signing out or
 * switching accounts drops the previous user's drafts and upload results.
 * Must sit inside SessionProvider.
 *
 * Props: children; queryClient (optional — tests pass their own).
 */

import type { ReactNode } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { QueryProvider } from "@/lib/query/QueryProvider";
import { useSession } from "@/lib/session";
import { UiStateProvider } from "@/lib/ui-state";
import { UploadProvider } from "@/lib/uploads";

export function AppDataProviders({ children, queryClient }: { children: ReactNode; queryClient?: QueryClient }) {
  const { user } = useSession();
  return (
    <QueryProvider client={queryClient}>
      <UiStateProvider key={user?.id ?? "signed-out"}>
        <UploadProvider>{children}</UploadProvider>
      </UiStateProvider>
    </QueryProvider>
  );
}
