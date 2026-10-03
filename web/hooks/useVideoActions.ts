/**
 * useVideoActions — Library video mutations other than upload (upload
 * lives in lib/uploads.tsx so it survives navigation) (Milestone 3.15).
 *
 * deleteVideo removes the video, then refetches the videos list so Home,
 * Library and the Queue's unscheduled list all drop it.
 */

import { useQueryClient } from "@tanstack/react-query";
import { deleteVideo } from "@/lib/api/videos";
import { queryKeys } from "@/lib/query/keys";
import { useApiAuth } from "@/hooks/useApiAuth";

export function useVideoActions() {
  const queryClient = useQueryClient();
  const { userId, accessToken } = useApiAuth();
  return {
    deleteVideo: async (videoId: number) => {
      await deleteVideo(accessToken, videoId);
      await queryClient.invalidateQueries({ queryKey: queryKeys.videos(userId) });
    },
  };
}
