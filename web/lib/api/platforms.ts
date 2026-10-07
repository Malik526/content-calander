/**
 * platforms.ts — typed calls to the real backend endpoints Milestone 3.6
 * added (src/content_automation/api/routes/me.py, platforms_tiktok.py),
 * plus Instagram's (platforms_instagram.py — status 4.0, connect and
 * disconnect 4.1).
 * The first real callers of lib/api/client.ts's apiRequest() — every
 * function here takes the caller's current accessToken
 * (`useSession().accessToken`) explicitly rather than reading it itself,
 * keeping client.ts (and this module) decoupled from lib/session.tsx.
 */

import { apiRequest } from "@/lib/api/client";
import type { ConnectStartResponse, CurrentUser, PlatformConnectionStatus, TikTokConnectionStatus } from "@/lib/api/types";

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

/** Milestone 4.0 — Instagram's connection status. */
export function getInstagramConnection(accessToken: string | null): Promise<PlatformConnectionStatus> {
  return apiRequest<PlatformConnectionStatus>("/api/platforms/instagram/status", { accessToken });
}

/**
 * Milestone 4.1 — starts an Instagram authorization attempt. returnTarget
 * is where the callback sends the browser afterwards; the server accepts
 * only exact allowlisted values (omit it for the web Settings page — a
 * future native client passes its own registered return URL).
 */
export function connectInstagram(accessToken: string | null, returnTarget?: string): Promise<ConnectStartResponse> {
  return apiRequest<ConnectStartResponse>("/api/platforms/instagram/connect", {
    method: "POST",
    accessToken,
    body: returnTarget === undefined ? undefined : { return_target: returnTarget },
  });
}

/** Milestone 4.1 — disconnects the caller's Instagram account; safe to repeat. */
export function disconnectInstagram(accessToken: string | null): Promise<PlatformConnectionStatus> {
  return apiRequest<PlatformConnectionStatus>("/api/platforms/instagram/disconnect", {
    method: "POST",
    accessToken,
  });
}
