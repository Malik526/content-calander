/**
 * useVideos — the signed-in user's videos (GET /api/videos), shared by
 * Home, Library and the Queue's unscheduled list so they never fetch or
 * disagree separately (Milestone 3.15).
 *
 * Returns the TanStack Query result; `data` is VideoResponse[]. Stale after
 * 30s. While any video is publishing it refetches every 15s, so the
 * worker's result shows up without a reload.
 */

import { useQuery } from "@tanstack/react-query";
import { listVideos } from "@/lib/api/videos";
import { isPublishInFlight } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export const VIDEOS_STALE_TIME_MS = 30_000;
export const PUBLISHING_POLL_INTERVAL_MS = 15_000;

export function useVideos() {
  const { userId, accessToken } = useApiAuth();
  return useQuery({
    queryKey: queryKeys.videos(userId),
    queryFn: async () => (accessToken ? (await listVideos(accessToken)).videos : []),
    staleTime: VIDEOS_STALE_TIME_MS,
    refetchInterval: (query) =>
      query.state.data?.some((video) => isPublishInFlight(video.publish_status)) ? PUBLISHING_POLL_INTERVAL_MS : false,
  });
}
