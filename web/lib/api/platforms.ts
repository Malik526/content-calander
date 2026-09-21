/**
 * platforms.ts — typed calls to the real backend endpoints Milestone 3.6
 * added (src/content_automation/api/routes/me.py, platforms_tiktok.py).
 * The first real callers of lib/api/client.ts's apiRequest() — every
 * function here takes the caller's current accessToken
 * (`useSession().accessToken`) explicitly rather than reading it itself,
 * keeping client.ts (and this module) decoupled from lib/session.tsx.
 */

import { apiRequest } from "@/lib/api/client";
import type { CurrentUser, TikTokConnectionStatus } from "@/lib/api/types";

export function getMe(accessToken: string | null): Promise<CurrentUser> {
  return apiRequest<CurrentUser>("/api/me", { accessToken });
}

export function getTikTokConnection(accessToken: string | null): Promise<TikTokConnectionStatus> {
  return apiRequest<TikTokConnectionStatus>("/api/platforms/tiktok/status", { accessToken });
}

export function connectTikTok(accessToken: string | null): Promise<{ authorization_url: string }> {
  return apiRequest<{ authorization_url: string }>("/api/platforms/tiktok/connect", {
    method: "POST",
    accessToken,
  });
}

export function disconnectTikTok(accessToken: string | null): Promise<TikTokConnectionStatus> {
  return apiRequest<TikTokConnectionStatus>("/api/platforms/tiktok/disconnect", {
    method: "POST",
    accessToken,
  });
}
