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

export type StatusTone = "pending" | "progress" | "success" | "danger" | "attention";

export const platformPostStatusPresentation: Record<PlatformPostStatus, { label: string; tone: StatusTone }> = {
  pending: { label: "Scheduled", tone: "pending" },
  publishing: { label: "Publishing", tone: "progress" },
  published: { label: "Published", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

/**
 * Queue/Calendar presentation of the backend's resolved display_status
 * (Milestone 3.11 — src/content_automation/publishing/publish_status.py is
 * the one resolver; this is the one presentation map, shared by
 * QueueSlotCard and QueueCalendarMonth so list and calendar can never
 * disagree). An unrecognized value is shown as "Needs attention" — never
 * as its raw string, and never as success.
 */
const QUEUE_STATUS_PRESENTATION: Record<string, { label: string; tone: StatusTone; dotClass: string }> = {
  OPEN: { label: "Open", tone: "pending", dotClass: "bg-status-pending" },
  SCHEDULED: { label: "Scheduled", tone: "progress", dotClass: "bg-status-progress" },
  PUBLISHING: { label: "Publishing", tone: "progress", dotClass: "bg-status-progress" },
  PUBLISHED: { label: "Published", tone: "success", dotClass: "bg-status-success" },
  FAILED: { label: "Failed", tone: "danger", dotClass: "bg-status-danger" },
  NEEDS_ATTENTION: { label: "Needs attention", tone: "attention", dotClass: "bg-status-attention" },
};

export function presentQueueStatus(displayStatus: string): { label: string; tone: StatusTone; dotClass: string } {
  return QUEUE_STATUS_PRESENTATION[displayStatus] ?? QUEUE_STATUS_PRESENTATION.NEEDS_ATTENTION;
}

/** Library filter tabs (Milestone 3.14 final follow-up). */
export type LibraryFilter = "all" | "unscheduled" | "scheduled" | "published";

/**
 * Which Library tab a video's backend publish_status belongs to. A pure
 * bucketing of the server-resolved status — no lifecycle logic here.
 * Scheduled holds everything in the workflow that hasn't published yet
 * (Scheduled, Publishing, Failed, Needs attention); the badge shows which.
 */
export function libraryFilterFor(publishStatus: string): Exclude<LibraryFilter, "all"> {
  if (publishStatus === "UNSCHEDULED") return "unscheduled";
  if (publishStatus === "PUBLISHED") return "published";
  return "scheduled";
}

/** Advisory next steps for a backend action_hint (Milestone 3.11). There is
 * deliberately no retry action — that arrives with Milestone 3.13. */
export function presentActionHint(actionHint: string | null): { label: string; href?: string } | null {
  switch (actionHint) {
    case "RECONNECT_ACCOUNT":
      return { label: "Reconnect in Settings", href: "/app/settings" };
    case "EDIT_CAPTION":
      return { label: "Edit the caption below." };
    default:
      // TRY_AGAIN_LATER is already said by the message itself.
      return null;
  }
}

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
