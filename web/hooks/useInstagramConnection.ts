/**
 * useInstagramConnection — Instagram connection status
 * (GET /api/platforms/instagram/status), cached per user like TikTok's
 * (Milestone 4.0).
 *
 * Returns the TanStack Query result; `data` is PlatformConnectionStatus.
 * Read-only until the 4.1 connect flow; stays fresh for 5 minutes like the
 * TikTok status, since it changes only through a connect round trip or a
 * disconnect.
 */

import { useQuery } from "@tanstack/react-query";
import { getInstagramConnection } from "@/lib/api/platforms";
import type { PlatformConnectionStatus } from "@/lib/api/types";
import { PlatformId } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";
import { TIKTOK_CONNECTION_STALE_TIME_MS } from "@/hooks/useTikTokConnection";

const NOT_CONNECTED: PlatformConnectionStatus = {
  platform: PlatformId.INSTAGRAM, connected: false, status: "DISCONNECTED", account_label: null, connect_available: false,
};

export function useInstagramConnection() {
  const { userId, accessToken } = useApiAuth();
  return useQuery({
    queryKey: queryKeys.platformConnection(userId, PlatformId.INSTAGRAM),
    queryFn: () => (accessToken ? getInstagramConnection(accessToken) : Promise.resolve(NOT_CONNECTED)),
    staleTime: TIKTOK_CONNECTION_STALE_TIME_MS,
  });
}
