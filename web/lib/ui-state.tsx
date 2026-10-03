"use client";

/**
 * ui-state.tsx — in-memory UI state that should survive moving between
 * /app screens (Milestone 3.15): the Library filter, the Queue's list/
 * calendar view and month, an unsaved cadence draft.
 *
 * Server data never goes here (that's TanStack Query's cache — see
 * docs/decisions/0017-client-server-state-cache.md). Nothing here touches
 * browser storage: it lives in memory for the session only, and
 * AppDataProviders remounts this provider when the signed-in user
 * changes, so one account's drafts never reach another.
 */

import { createContext, useContext, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";

const UiStateContext = createContext<Map<string, unknown> | null>(null);

export function UiStateProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new Map<string, unknown>());
  return <UiStateContext.Provider value={store}>{children}</UiStateContext.Provider>;
}

/**
 * useState whose value outlives the component: remounting with the same
 * key restores the last value. Returns [value, setValue] like useState.
 */
export function usePersistentUiState<T>(key: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const store = useContext(UiStateContext);
  if (store === null) {
    throw new Error("usePersistentUiState must be used within a UiStateProvider (see components/app/AppDataProviders.tsx).");
  }
  const [value, setValue] = useState<T>(() => {
    if (store.has(key)) return store.get(key) as T;
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });

  useEffect(() => {
    store.set(key, value);
  }, [store, key, value]);

  return [value, setValue];
}
