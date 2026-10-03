/**
 * useCadence — the user's posting cadence (GET /api/cadence), shared by
 * Home's checklist and the Queue's rhythm editor (Milestone 3.15).
 *
 * Returns the TanStack Query result; `data` is CadenceResponse. It only
 * changes when this user saves it, so it stays fresh for 5 minutes and
 * useSaveCadence writes the saved value straight into the cache.
 */

import { useQuery } from "@tanstack/react-query";
import { getCadence } from "@/lib/api/cadence";
import type { CadenceResponse } from "@/lib/api/types";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export const CADENCE_STALE_TIME_MS = 5 * 60_000;

const UNCONFIGURED: CadenceResponse = { configured: false, timezone: null, is_active: false, posting_times: [] };

export function useCadence() {
  const { userId, accessToken } = useApiAuth();
  return useQuery({
    queryKey: queryKeys.cadence(userId),
    queryFn: () => (accessToken ? getCadence(accessToken) : Promise.resolve(UNCONFIGURED)),
    staleTime: CADENCE_STALE_TIME_MS,
  });
}
