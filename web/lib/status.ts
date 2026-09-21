/**
 * status.ts — presentation helpers for backend lifecycle states
 * (Milestone 3.5, Phase 19).
 *
 * The UI never displays a raw backend enum string (PENDING, PUBLISHING,
 * PUBLISHED, FAILED — see docs/decisions/0006-tiktok-publisher-foundation.md
 * for where those come from) or duplicates this label/color logic per
 * page — every place that shows a platform-post status imports from here.
 * This does not redesign backend state semantics; it only presents them.
 */

import type { PlatformPostStatus } from "@/lib/api/types";

export type StatusTone = "pending" | "progress" | "success" | "danger";

export const platformPostStatusPresentation: Record<PlatformPostStatus, { label: string; tone: StatusTone }> = {
  pending: { label: "Scheduled", tone: "pending" },
  publishing: { label: "Publishing", tone: "progress" },
  published: { label: "Published", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

export function formatScheduledAt(iso: string | null): string {
  if (!iso) return "Not scheduled";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Not scheduled";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
