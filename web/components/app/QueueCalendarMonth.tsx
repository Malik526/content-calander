import type { QueueSlotResponse } from "@/lib/api/types";

const WEEKDAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const DOT_TONE: Record<string, string> = {
  OPEN: "bg-status-pending",
  ASSIGNED: "bg-status-progress",
  PUBLISHING: "bg-status-progress",
  PUBLISHED: "bg-status-success",
  FAILED: "bg-status-danger",
};

/** scheduled_at is naive-local (see QueueSlotCard's own note) — parsed as
 * local wall-clock time, so grouping by these date components groups by
 * the same calendar day a human reading "Mon, Oct 5, 9:00 AM" would. */
function localDateKey(iso: string): string {
  const date = new Date(iso);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function mondayFirstWeekday(date: Date): number {
  return (date.getDay() + 6) % 7; // JS getDay() is Sunday=0 — shift to Monday=0
}

/**
 * QueueCalendarMonth — a plain month grid (Milestone 3.9: Queue + Calendar
 * Functionality). Each day cell shows its date number and one small
 * clickable dot per slot that day, colored by display_status; clicking a
 * dot selects that slot for QueueBoard's detail panel (a QueueSlotCard)
 * below. No drag/drop, no week view, no per-cell editing — a functional
 * "which dates have slots, which are open, which are taken" view only, per
 * the milestone's own scope ("does not need final branding or
 * sophisticated interactions").
 */
export function QueueCalendarMonth({
  month,
  slots,
  selectedSlotId,
  onSelectSlot,
  onPrevMonth,
  onNextMonth,
}: {
  month: Date;
  slots: QueueSlotResponse[];
  selectedSlotId: number | null;
  onSelectSlot: (slotId: number) => void;
  onPrevMonth: () => void;
  onNextMonth: () => void;
}) {
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const firstOfMonth = new Date(year, monthIndex, 1);
  const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
  const leadingBlanks = mondayFirstWeekday(firstOfMonth);

  const slotsByDay = new Map<string, QueueSlotResponse[]>();
  for (const slot of slots) {
    const key = localDateKey(slot.scheduled_at);
    const existing = slotsByDay.get(key);
    if (existing) existing.push(slot);
    else slotsByDay.set(key, [slot]);
  }

  const cells: Array<{ day: number } | null> = [
    ...Array.from({ length: leadingBlanks }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => ({ day: i + 1 })),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onPrevMonth}
          aria-label="Previous month"
          className="rounded-lg border border-border px-2.5 py-1 text-sm text-ink hover:border-accent/40 hover:text-accent"
        >
          ‹
        </button>
        <p className="text-sm font-medium text-ink">
          {month.toLocaleString(undefined, { month: "long", year: "numeric" })}
        </p>
        <button
          type="button"
          onClick={onNextMonth}
          aria-label="Next month"
          className="rounded-lg border border-border px-2.5 py-1 text-sm text-ink hover:border-accent/40 hover:text-accent"
        >
          ›
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-xs text-ink-muted">
        {WEEKDAY_HEADERS.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell, index) => {
          if (cell === null) return <div key={`blank-${index}`} />;
          const daySlots = slotsByDay.get(`${year}-${monthIndex}-${cell.day}`) ?? [];
          return (
            <div key={cell.day} className="flex min-h-16 flex-col items-center gap-1 rounded-lg border border-border p-1.5">
              <span className="text-xs text-ink-muted">{cell.day}</span>
              <div className="flex flex-wrap justify-center gap-1">
                {daySlots.map((slot) => (
                  <button
                    key={slot.id}
                    type="button"
                    onClick={() => onSelectSlot(slot.id)}
                    aria-label={`Slot on ${month.toLocaleString(undefined, { month: "long" })} ${cell.day}, ${slot.display_status.toLowerCase()}`}
                    aria-pressed={selectedSlotId === slot.id}
                    className={`h-2.5 w-2.5 rounded-full ${DOT_TONE[slot.display_status] ?? "bg-status-pending"} ${
                      selectedSlotId === slot.id ? "ring-2 ring-offset-1 ring-accent" : ""
                    }`}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
