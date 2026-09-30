/**
 * captions.ts — typed calls to the /api/videos/{id}/caption endpoints
 * Milestone 3.10 (Caption Generation + Editing) added
 * (src/content_automation/api/routes/captions.py). Same pattern as
 * lib/api/queue.ts: every function takes the caller's accessToken
 * explicitly rather than reading it itself.
 */

import { apiRequest } from "@/lib/api/client";
import type { CaptionResponse } from "@/lib/api/types";

export function getCaption(accessToken: string | null, videoId: number): Promise<CaptionResponse> {
  return apiRequest<CaptionResponse>(`/api/videos/${videoId}/caption`, { accessToken });
}

/** Save a user-edited caption; an empty string clears it. */
export function saveCaption(accessToken: string | null, videoId: number, captionText: string): Promise<CaptionResponse> {
  return apiRequest<CaptionResponse>(`/api/videos/${videoId}/caption`, {
    method: "PUT", body: { caption_text: captionText }, accessToken,
  });
}

/** Generate and save a caption. The backend refuses (409) to replace an
 * existing caption unless overwrite is true. */
export function generateCaption(accessToken: string | null, videoId: number, overwrite: boolean): Promise<CaptionResponse> {
  return apiRequest<CaptionResponse>(`/api/videos/${videoId}/caption/generate`, {
    method: "POST", body: { overwrite }, accessToken,
  });
}
