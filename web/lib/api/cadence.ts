/**
 * cadence.ts — typed calls to the real backend endpoints Milestone 3.8
 * added (src/content_automation/api/routes/cadence.py). Same shape as
 * lib/api/platforms.ts — every function takes the caller's current
 * accessToken explicitly rather than reading it itself.
 */

import { apiRequest } from "@/lib/api/client";
import type { CadenceRequest, CadenceResponse, SlotListResponse } from "@/lib/api/types";

export function getCadence(accessToken: string | null): Promise<CadenceResponse> {
  return apiRequest<CadenceResponse>("/api/cadence", { accessToken });
}

export function saveCadence(accessToken: string | null, body: CadenceRequest): Promise<CadenceResponse> {
  return apiRequest<CadenceResponse>("/api/cadence", { method: "PUT", body, accessToken });
}

export function getUpcomingSlots(accessToken: string | null): Promise<SlotListResponse> {
  return apiRequest<SlotListResponse>("/api/cadence/slots", { accessToken });
}
