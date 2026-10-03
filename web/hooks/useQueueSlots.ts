/**
 * useQueueSlots — Queue slots for one window (GET /api/queue/slots), keyed
 * by that window so each month is cached separately (Milestone 3.15).
 *
 * Returns the TanStack Query result; `data` is QueueSlotResponse[]. While a
 * new month loads, the previous month stays on screen instead of a
 * spinner. Stale after 30s; polls every 15s while any slot is publishing.
 */

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { listQueueSlots } from "@/lib/api/queue";
import { isPublishInFlight } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";
import { PUBLISHING_POLL_INTERVAL_MS } from "@/hooks/useVideos";

export const QUEUE_STALE_TIME_MS = 30_000;

export function useQueueSlots(from: string, to: string) {
  const { userId, accessToken } = useApiAuth();
  return useQuery({
    queryKey: queryKeys.queueSlots(userId, from, to),
    queryFn: async () => (accessToken ? (await listQueueSlots(accessToken, from, to)).slots : []),
    staleTime: QUEUE_STALE_TIME_MS,
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.some((slot) => isPublishInFlight(slot.display_status)) ? PUBLISHING_POLL_INTERVAL_MS : false,
  });
}
