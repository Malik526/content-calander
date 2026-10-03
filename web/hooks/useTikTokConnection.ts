/**
 * useTikTokConnection — TikTok connection status
 * (GET /api/platforms/tiktok/status), shared by Home and Settings
 * (Milestone 3.15).
 *
 * Returns the TanStack Query result; `data` is TikTokConnectionStatus. It
 * changes only through Connect (a full-page OAuth round trip, which starts
 * a fresh cache) or Disconnect (written into the cache by
 * useTikTokActions), so it stays fresh for 5 minutes.
 */

import { useQuery } from "@tanstack/react-query";
import { getTikTokConnection } from "@/lib/api/platforms";
import type { TikTokConnectionStatus } from "@/lib/api/types";
import { PlatformId } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export const TIKTOK_CONNECTION_STALE_TIME_MS = 5 * 60_000;

const NOT_CONNECTED: TikTokConnectionStatus = {
  platform: PlatformId.TIKTOK, connected: false, status: "not_connected", account_label: null,
};

export function useTikTokConnection() {
  const { userId, accessToken } = useApiAuth();
  return useQuery({
    queryKey: queryKeys.tiktokConnection(userId),
    queryFn: () => (accessToken ? getTikTokConnection(accessToken) : Promise.resolve(NOT_CONNECTED)),
    staleTime: TIKTOK_CONNECTION_STALE_TIME_MS,
  });
}
