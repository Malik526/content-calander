/**
 * publishing.ts — the product's publishing vocabulary as plain TypeScript
 * (Milestone 3.15). No React, Next.js or DOM imports, so a future Expo
 * client can reuse or copy it unchanged.
 *
 * Values mirror the backend: publishing/publish_status.py (display
 * statuses) and config.TARGET_PUBLISHING_PLATFORMS (platform ids). The
 * backend stays the source of truth; an unrecognized value from a newer
 * API must still be handled by callers (see lib/status.ts's fallback).
 */

/** Resolved publish states, from publish_status.resolve_*_publish_status. */
export const PublishStatus = {
  /** Library only: the video holds no slot. */
  UNSCHEDULED: "UNSCHEDULED",
  /** Queue only: the slot holds no video. */
  OPEN: "OPEN",
  SCHEDULED: "SCHEDULED",
  PUBLISHING: "PUBLISHING",
  PUBLISHED: "PUBLISHED",
  FAILED: "FAILED",
  NEEDS_ATTENTION: "NEEDS_ATTENTION",
} as const;

export type PublishStatusValue = (typeof PublishStatus)[keyof typeof PublishStatus];

/** Publishing platform identifiers (platform_posts.platform). */
export const PlatformId = {
  TIKTOK: "tiktok",
  /** Milestone 4.0: registered; connecting since 4.1, publishing arrives in 4.2. */
  INSTAGRAM: "instagram",
} as const;

export type PlatformIdValue = (typeof PlatformId)[keyof typeof PlatformId];

/** Whether a status can still change without user action, so clients
 * should poll it rather than trust a cached value for long. */
export function isPublishInFlight(status: string | null | undefined): boolean {
  return status === PublishStatus.PUBLISHING;
}
