/**
 * videos.ts — typed calls to the real /api/videos endpoints Milestone 3.7
 * added (src/content_automation/api/routes/videos.py). Same pattern as
 * lib/api/platforms.ts: every function takes the caller's current
 * accessToken explicitly rather than reading it itself.
 */

import { apiRequest } from "@/lib/api/client";
import type { VideoListResponse, VideoUploadBatchResponse } from "@/lib/api/types";

export function listVideos(accessToken: string | null): Promise<VideoListResponse> {
  return apiRequest<VideoListResponse>("/api/videos", { accessToken });
}

/**
 * Uploads every file in one multipart request — a real "select multiple,
 * upload" batch, not one request per file. `files` is a real FileList/File[]
 * from an `<input type="file" multiple>` — see components/forms/VideoUploadForm.tsx.
 */
export function uploadVideos(accessToken: string | null, files: FileList | File[]): Promise<VideoUploadBatchResponse> {
  const formData = new FormData();
  for (const file of Array.from(files)) {
    formData.append("files", file);
  }
  return apiRequest<VideoUploadBatchResponse>("/api/videos", {
    method: "POST",
    body: formData,
    accessToken,
  });
}

/**
 * Deletes one of the caller's own videos (Milestone 3.7 follow-up — the
 * Library's delete action). Resolves to nothing on success (the backend
 * returns 204 No Content — see lib/api/client.ts's own handling of that
 * status). Rejects with an ApiError the caller should show directly:
 * 404 if the video does not exist or isn't the caller's own, 409 if it's
 * still assigned to a schedule/queue entry (see api/routes/videos.py).
 */
export function deleteVideo(accessToken: string | null, videoId: number): Promise<void> {
  return apiRequest<void>(`/api/videos/${videoId}`, { method: "DELETE", accessToken });
}
