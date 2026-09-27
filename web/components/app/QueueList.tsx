import { QueueSlotCard } from "@/components/app/QueueSlotCard";
import type { QueueSlotResponse, VideoResponse } from "@/lib/api/types";

/**
 * QueueList — the "list toggle" alongside QueueCalendarMonth (Milestone
 * 3.9: Queue + Calendar Functionality). Every slot in the current window,
 * each rendered with QueueSlotCard's full detail + actions — no separate
 * detail panel is needed here, unlike calendar mode, since every row
 * already shows everything.
 */
export function QueueList({
  slots,
  unassignedVideos,
  busySlotId,
  onAssign,
  onRemove,
}: {
  slots: QueueSlotResponse[];
  unassignedVideos: VideoResponse[];
  busySlotId: number | null;
  onAssign: (slotId: number, videoId: number) => void;
  onRemove: (slotId: number) => void;
}) {
  if (slots.length === 0) {
    return <p className="text-sm text-ink-muted">No slots in this window yet.</p>;
  }

  return (
    <ul className="flex flex-col gap-3">
      {slots.map((slot) => (
        <li key={slot.id}>
          <QueueSlotCard
            slot={slot}
            unassignedVideos={unassignedVideos}
            busy={busySlotId === slot.id}
            onAssign={(videoId) => onAssign(slot.id, videoId)}
            onRemove={() => onRemove(slot.id)}
          />
        </li>
      ))}
    </ul>
  );
}
