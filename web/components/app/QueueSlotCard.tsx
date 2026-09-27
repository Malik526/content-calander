"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import type { StatusTone } from "@/lib/status";
import type { QueueSlotResponse, VideoResponse } from "@/lib/api/types";

const DISPLAY_STATUS_PRESENTATION: Record<string, { label: string; tone: StatusTone }> = {
  OPEN: { label: "Open", tone: "pending" },
  ASSIGNED: { label: "Assigned", tone: "progress" },
  PUBLISHING: { label: "Publishing", tone: "progress" },
  PUBLISHED: { label: "Published", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
};

function formatSlotDateTime(iso: string): string {
  // scheduled_at is naive-local (see lib/api/types.ts's own QueueSlotResponse
  // note) — new Date() on a bare ISO string with no trailing Z/offset
  // parses it as local wall-clock time, so this reformats the same numbers
  // rather than converting between timezones (same reasoning as the
  // now-retired UpcomingSchedulePreview it replaces).
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/**
 * QueueSlotCard — full detail + actions for exactly one content_slot
 * (Milestone 3.9: Queue + Calendar Functionality). Used both as each row
 * in QueueList and as the selected-slot detail panel under
 * QueueCalendarMonth, so assign/remove logic lives in exactly one place.
 *
 * Props:
 *   slot — the real GET /api/queue/slots row.
 *   unassignedVideos — the caller's own videos with no assigned_slot_id
 *     yet (Library videos eligible for manual assignment to an OPEN slot).
 *   onAssign(videoId) — manual assignment; only rendered for an OPEN slot.
 *   onRemove() — "Remove from schedule"; only rendered for a slot whose
 *     display_status is still "ASSIGNED" (nothing has actually published
 *     or failed yet — see PlatformPostInProgressError's backend guard,
 *     which this UI mirrors by simply not offering the action once it
 *     would be refused anyway).
 *   busy — disables both actions while a request for this slot is in flight.
 */
export function QueueSlotCard({
  slot,
  unassignedVideos,
  onAssign,
  onRemove,
  busy = false,
}: {
  slot: QueueSlotResponse;
  unassignedVideos: VideoResponse[];
  onAssign: (videoId: number) => void;
  onRemove: () => void;
  busy?: boolean;
}) {
  const [pendingVideoId, setPendingVideoId] = useState<string>("");
  const presentation = DISPLAY_STATUS_PRESENTATION[slot.display_status] ?? { label: slot.display_status, tone: "pending" as StatusTone };

  return (
    <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink">{formatSlotDateTime(slot.scheduled_at)}</p>
        {slot.assigned_video ? (
          <p className="mt-0.5 truncate text-xs text-ink-muted">{slot.assigned_video.original_filename}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <Badge tone={presentation.tone}>{presentation.label}</Badge>

        {slot.status === "OPEN" ? (
          unassignedVideos.length === 0 ? (
            <p className="text-xs text-ink-muted">No unscheduled videos</p>
          ) : (
            <div className="flex items-center gap-2">
              <select
                value={pendingVideoId}
                onChange={(event) => setPendingVideoId(event.target.value)}
                aria-label={`Video to assign to ${formatSlotDateTime(slot.scheduled_at)}`}
                className="rounded-lg border border-border bg-surface px-2 py-1.5 text-sm text-ink"
              >
                <option value="">Choose a video…</option>
                {unassignedVideos.map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.original_filename}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={!pendingVideoId || busy}
                onClick={() => onAssign(Number(pendingVideoId))}
                className="rounded-lg border border-border bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-accent/40 hover:text-accent disabled:opacity-60"
              >
                Assign
              </button>
            </div>
          )
        ) : slot.display_status === "ASSIGNED" ? (
          <button
            type="button"
            disabled={busy}
            onClick={onRemove}
            className="text-xs font-medium text-ink-muted hover:text-status-danger disabled:opacity-60"
          >
            Remove from schedule
          </button>
        ) : null}
      </div>
    </Card>
  );
}
