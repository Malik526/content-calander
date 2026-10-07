/**
 * useInstagramActions — Instagram connect/disconnect (Milestone 4.1).
 *
 * Returns:
 *   startConnect()       requests a fresh authorization attempt and returns
 *                        Instagram's authorization URL; the caller (a web
 *                        adapter) decides how to open it, like TikTok's.
 *   disconnect()         disconnects and writes the returned status into the
 *                        cache, so every reader updates at once.
 *   refreshConnection()  invalidates the cached status, e.g. after the OAuth
 *                        callback lands back on Settings.
 *
 * Every action updates or invalidates
 * queryKeys.platformConnection(userId, PlatformId.INSTAGRAM) — the Milestone
 * 3.15 cache contract (docs/decisions/0017-client-server-state-cache.md).
 * Instagram and TikTok actions share no state.
 */

import { useQueryClient } from "@tanstack/react-query";
import { connectInstagram, disconnectInstagram } from "@/lib/api/platforms";
import { PlatformId } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export function useInstagramActions() {
  const queryClient = useQueryClient();
  const { userId, accessToken } = useApiAuth();
  const key = queryKeys.platformConnection(userId, PlatformId.INSTAGRAM);
  const refreshConnection = () => queryClient.invalidateQueries({ queryKey: key });

  return {
    startConnect: async () => {
      const { authorization_url } = await connectInstagram(accessToken);
      // A pending attempt doesn't change the status, but connect is still
      // a mutation: mark the cached status stale so it's re-read on return.
      void refreshConnection();
      return authorization_url;
    },
    disconnect: async () => {
      const status = await disconnectInstagram(accessToken);
      queryClient.setQueryData(key, status);
      return status;
    },
    refreshConnection,
  };
}
