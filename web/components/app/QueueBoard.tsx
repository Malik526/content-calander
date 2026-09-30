"use client";

import { useEffect, useState } from "react";
import { QueueCalendarMonth } from "@/components/app/QueueCalendarMonth";
import { QueueList } from "@/components/app/QueueList";
import { QueueSlotCard } from "@/components/app/QueueSlotCard";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api/client";
import { generateCaption, saveCaption } from "@/lib/api/captions";
import { assignNextOpenSlot, assignVideoToSlot, listQueueSlots, unassignSlot } from "@/lib/api/queue";
import { listVideos } from "@/lib/api/videos";
import type { CaptionResponse, QueueSlotResponse, VideoResponse } from "@/lib/api/types";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** A naive-local "YYYY-MM-DDTHH:mm:ss" string from a Date's own local
 * components — Date.toISOString() converts to UTC first, which is wrong
 * here: scheduled_at (and the window this compares it against) is
 * naive-local, matching every other slot-related timestamp in this
 * codebase (see AGENTS.md's timestamp-convention note). */
function toNaiveIso(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function monthWindow(monthCursor: Date): { from: string; to: string } {
  const year = monthCursor.getFullYear();
  const month = monthCursor.getMonth();
  return {
    from: toNaiveIso(new Date(year, month, 1, 0, 0, 0)),
    to: toNaiveIso(new Date(year, month + 1, 1, 0, 0, 0)),
  };
}

/**
 * QueueBoard — the real Queue: a list/calendar toggle over GET
 * /api/queue/slots for the currently displayed month, an "Unscheduled
 * videos" section for automatic (FIFO) assignment, and per-slot manual
 * assign/"Remove from schedule" actions (Milestone 3.9: Queue + Calendar
 * Functionality). Composes three pure presentational pieces:
 * QueueList/QueueCalendarMonth (the two view modes) and QueueSlotCard
 * (one slot's full detail + actions — used as every List row and as
 * Calendar mode's selected-slot detail panel, so assign/remove logic
 * lives in exactly one place).
 *
 * Milestone 3.10: each assigned slot also shows its video's caption
 * (CaptionEditor, inside QueueSlotCard). Caption saves patch the one
 * affected slot in place rather than reloading the whole board, so other
 * slots' unsaved caption drafts survive.
 *
 * No drag/drop, no per-slot editing beyond assign/remove, no manual
 * one-off slot creation — all explicitly deferred to a later milestone
 * per this milestone's own brief.
 */
export function QueueBoard({ accessToken }: { accessToken: string | null }) {
  const [viewMode, setViewMode] = useState<"list" | "calendar">("list");
  const [monthCursor, setMonthCursor] = useState(() => new Date());
  const [slots, setSlots] = useState<QueueSlotResponse[] | null>(null);
  const [videos, setVideos] = useState<VideoResponse[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedSlotId, setSelectedSlotId] = useState<number | null>(null);
  const [busySlotId, setBusySlotId] = useState<number | null>(null);
  const [busyVideoId, setBusyVideoId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function load() {
    setLoadError(null);
    try {
      const { from, to } = monthWindow(monthCursor);
      const [slotsResult, videosResult] = await Promise.all([
        listQueueSlots(accessToken, from, to),
        listVideos(accessToken),
      ]);
      setSlots(slotsResult.slots);
      setVideos(videosResult.videos);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : "Could not load your queue.");
    }
  }

  useEffect(() => {
    if (!accessToken) {
      // No real backend to call at all in this case (dev-mock session —
      // see components/app/QueueScheduling.tsx's identical handling).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSlots([]);
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, monthCursor]);

  const unassignedVideos = videos.filter((video) => video.assigned_slot_id === null);
  const selectedSlot = slots?.find((slot) => slot.id === selectedSlotId) ?? null;

  async function handleAssign(slotId: number, videoId: number) {
    setActionError(null);
    setBusySlotId(slotId);
    try {
      await assignVideoToSlot(accessToken, slotId, videoId);
      await load();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Could not assign that video.");
    } finally {
      setBusySlotId(null);
    }
  }

  async function handleAssignNext(videoId: number) {
    setActionError(null);
    setBusyVideoId(videoId);
    try {
      await assignNextOpenSlot(accessToken, videoId);
      await load();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Could not assign that video to the next open slot.");
    } finally {
      setBusyVideoId(null);
    }
  }

  async function handleRemove(slotId: number) {
    setActionError(null);
    setBusySlotId(slotId);
    try {
      await unassignSlot(accessToken, slotId);
      await load();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Could not remove this video from the schedule.");
    } finally {
      setBusySlotId(null);
    }
  }

  // Caption errors propagate to CaptionEditor, which shows them inline.
  function applyCaption(result: CaptionResponse): CaptionResponse {
    setSlots((current) =>
      current?.map((slot) =>
        slot.assigned_video?.id === result.video_id
          ? { ...slot, assigned_video: { ...slot.assigned_video, caption: result } }
          : slot,
      ) ?? current,
    );
    return result;
  }

  async function handleSaveCaption(videoId: number, text: string): Promise<CaptionResponse> {
    return applyCaption(await saveCaption(accessToken, videoId, text));
  }

  async function handleGenerateCaption(videoId: number, overwrite: boolean): Promise<CaptionResponse> {
    return applyCaption(await generateCaption(accessToken, videoId, overwrite));
  }

  if (loadError) {
    return <ErrorState message={loadError} onRetry={load} />;
  }

  if (slots === null) {
    return (
      <Card>
        <Spinner label="Loading your queue…" />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {actionError ? <ErrorState message={actionError} /> : null}

      <section aria-labelledby="unscheduled-videos-heading">
        <h3 id="unscheduled-videos-heading" className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Unscheduled videos
        </h3>
        {unassignedVideos.length === 0 ? (
          <p className="text-sm text-ink-muted">No unscheduled videos — upload one in Library.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {unassignedVideos.map((video) => (
              <li key={video.id} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
                <p className="truncate text-sm text-ink">{video.original_filename}</p>
                <button
                  type="button"
                  disabled={busyVideoId === video.id}
                  onClick={() => void handleAssignNext(video.id)}
                  className="shrink-0 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:border-accent/40 hover:text-accent disabled:opacity-60"
                >
                  {busyVideoId === video.id ? "Assigning…" : "Assign to next available slot"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setViewMode("list")}
          aria-pressed={viewMode === "list"}
          className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
            viewMode === "list" ? "border-accent text-accent" : "border-border text-ink-muted"
          }`}
        >
          List
        </button>
        <button
          type="button"
          onClick={() => setViewMode("calendar")}
          aria-pressed={viewMode === "calendar"}
          className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
            viewMode === "calendar" ? "border-accent text-accent" : "border-border text-ink-muted"
          }`}
        >
          Calendar
        </button>
      </div>

      {viewMode === "list" ? (
        <QueueList
          slots={slots}
          unassignedVideos={unassignedVideos}
          busySlotId={busySlotId}
          onAssign={(slotId, videoId) => void handleAssign(slotId, videoId)}
          onRemove={(slotId) => void handleRemove(slotId)}
          onSaveCaption={handleSaveCaption}
          onGenerateCaption={handleGenerateCaption}
        />
      ) : (
        <div className="flex flex-col gap-4">
          <QueueCalendarMonth
            month={monthCursor}
            slots={slots}
            selectedSlotId={selectedSlotId}
            onSelectSlot={setSelectedSlotId}
            onPrevMonth={() => setMonthCursor((current) => new Date(current.getFullYear(), current.getMonth() - 1, 1))}
            onNextMonth={() => setMonthCursor((current) => new Date(current.getFullYear(), current.getMonth() + 1, 1))}
          />
          {selectedSlot ? (
            <QueueSlotCard
              slot={selectedSlot}
              unassignedVideos={unassignedVideos}
              busy={busySlotId === selectedSlot.id}
              onAssign={(videoId) => void handleAssign(selectedSlot.id, videoId)}
              onRemove={() => void handleRemove(selectedSlot.id)}
              onSaveCaption={handleSaveCaption}
              onGenerateCaption={handleGenerateCaption}
            />
          ) : (
            <p className="text-sm text-ink-muted">Select a date&apos;s slot to see its details.</p>
          )}
        </div>
      )}
    </div>
  );
}
