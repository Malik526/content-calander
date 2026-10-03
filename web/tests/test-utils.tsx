/**
 * test-utils.tsx — renders /app screens inside the same client-state stack
 * the real layout uses (components/app/AppDataProviders.tsx), with a fresh
 * TanStack Query client per test so no cached server state leaks between
 * tests (Milestone 3.15). Retries are off so a mocked failure surfaces at
 * once. Returns render()'s result plus the queryClient.
 */

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { AppDataProviders } from "@/components/app/AppDataProviders";
import { createAppQueryClient } from "@/lib/query/queryClient";

export function createTestQueryClient(): QueryClient {
  return createAppQueryClient({ retry: false });
}

export function renderWithProviders(ui: ReactElement, { queryClient = createTestQueryClient() }: { queryClient?: QueryClient } = {}) {
  const result = render(<AppDataProviders queryClient={queryClient}>{ui}</AppDataProviders>);
  return {
    ...result,
    queryClient,
    rerenderWithProviders: (next: ReactElement) =>
      result.rerender(<AppDataProviders queryClient={queryClient}>{next}</AppDataProviders>),
  };
}
