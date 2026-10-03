/**
 * useTikTokActions — TikTok connect/disconnect (Milestone 3.15).
 *
 * startConnect returns the platform's authorization URL; the caller (a web
 * adapter) decides how to open it — Settings navigates the page, a native
 * client would open a secure auth session. Disconnect writes the returned
 * status into the cache, so Settings and Home update together.
 */

import { useQueryClient } from "@tanstack/react-query";
import { connectTikTok, disconnectTikTok } from "@/lib/api/platforms";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export function useTikTokActions() {
  const queryClient = useQueryClient();
  const { userId, accessToken } = useApiAuth();
  return {
    startConnect: async () => (await connectTikTok(accessToken)).authorization_url,
    disconnect: async () => {
      const status = await disconnectTikTok(accessToken);
      queryClient.setQueryData(queryKeys.tiktokConnection(userId), status);
      return status;
    },
  };
}
