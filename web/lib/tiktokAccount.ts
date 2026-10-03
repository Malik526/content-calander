/**
 * tiktokAccount.ts — which TikTok account a connection is authorized as,
 * for display (Milestone 3.14 follow-up). The backend fills
 * creator_username/creator_nickname from TikTok's creator_info; either can
 * be null if TikTok couldn't be asked just then (the connection itself is
 * still reported as connected).
 */

import type { TikTokConnectionStatus } from "@/lib/api/types";

/** "@username", else the nickname, else account_label, else null (show plain "Connected"). */
export function connectedAccountName(connection: TikTokConnectionStatus): string | null {
  if (!connection.connected) return null;
  if (connection.creator_username) return `@${connection.creator_username}`;
  return connection.creator_nickname ?? connection.account_label ?? null;
}
