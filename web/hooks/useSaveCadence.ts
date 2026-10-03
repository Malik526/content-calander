/**
 * useSaveCadence — saves the posting cadence (PUT /api/cadence) and keeps
 * the cache consistent (Milestone 3.15): the saved cadence goes straight
 * into the cache, and every cached Queue window is invalidated because a
 * save regenerates future slots.
 *
 * Returns a TanStack mutation; call mutateAsync(request).
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { saveCadence } from "@/lib/api/cadence";
import type { CadenceRequest } from "@/lib/api/types";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export function useSaveCadence() {
  const queryClient = useQueryClient();
  const { userId, accessToken } = useApiAuth();
  return useMutation({
    mutationFn: (request: CadenceRequest) => saveCadence(accessToken, request),
    onSuccess: async (saved) => {
      queryClient.setQueryData(queryKeys.cadence(userId), saved);
      await queryClient.invalidateQueries({ queryKey: queryKeys.queueSlotsAll(userId) });
    },
  });
}
