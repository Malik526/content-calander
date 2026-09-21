/**
 * types.ts — frontend domain types (Milestone 3.5, Phase 13).
 *
 * These represent API/product concepts, not raw database columns — the UI
 * never imports or assumes a Postgres/SQLite schema shape directly (see
 * docs/decisions/0010-frontend-app-shell.md "Shared Types"). Field names
 * and shapes here are deliberately looser/friendlier than the backend's
 * own `videos`/`platform_posts` tables (e.g. `PlatformPostStatus` is
 * lowercase and only carries the four states the UI actually needs to
 * distinguish, not every backend column like retry_count or
 * next_status_check_at).
 */

export type PlatformPostStatus = "pending" | "publishing" | "published" | "failed";

export type Platform = "tiktok";

export type VideoStatus = "processing" | "ready" | "needs_review" | "failed";

export interface VideoSummary {
  id: string;
  title: string;
  status: VideoStatus;
  durationSeconds: number | null;
  createdAt: string;
}

export interface PlatformPostSummary {
  id: string;
  videoId: string;
  platform: Platform;
  status: PlatformPostStatus;
  scheduledAt: string | null;
  publishedAt: string | null;
}

export interface QueueItem {
  id: string;
  videoTitle: string;
  platform: Platform;
  status: PlatformPostStatus;
  scheduledAt: string | null;
}

export interface PlatformConnectionSummary {
  id: string;
  platform: Platform;
  accountLabel: string | null;
  status: "connected" | "disconnected" | "needs_attention";
}

/**
 * The real GET /api/me and /api/platforms/tiktok/status response shapes
 * (Milestone 3.6) — see api/schemas/me.py and api/schemas/platforms.py on
 * the backend. Deliberately mirrors those Pydantic models' field names
 * (snake_case, matching the backend's actual JSON) rather than
 * reformatting to camelCase, since these are read directly from the wire
 * with no transform layer in between — unlike PlatformConnectionSummary
 * above, which is a UI-only shape for still-mocked data.
 */
export interface CurrentUser {
  id: number;
  email: string;
  display_name: string | null;
}

export interface TikTokConnectionStatus {
  platform: "tiktok";
  connected: boolean;
  status: string;
  account_label: string | null;
}
