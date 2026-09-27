import type { SlotResponse } from "@/lib/api/types";

function formatSlot(iso: string): string {
  // scheduled_at is a naive-local string in the cadence's own timezone
  // (see AGENTS.md's timestamp-convention note) — `new Date(...)` on a
  // bare "YYYY-MM-DDTHH:MM:SS" string (no trailing Z/offset) parses it as
  // local wall-clock time, so this reformats the same wall-clock numbers
  // rather than converting between timezones.
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusLabel(status: string): string | null {
  // OPEN is every slot's normal, unclaimed state today (no assignment
  // endpoint exists yet — Milestone 3.9) — surfacing it adds no
  // information a user can act on, so only a real, meaningful status is
  // shown at all (see docs brief for Milestone 3.8.1).
  if (status === "OPEN") return null;
  return status.charAt(0) + status.slice(1).toLowerCase();
}

/**
 * UpcomingSchedulePreview — human-readable rendering of GET
 * /api/cadence/slots (Milestone 3.8.1). Replaces the earlier raw
 * ISO-timestamp + "OPEN" list that shipped with Milestone 3.8's Settings
 * section.
 */
export function UpcomingSchedulePreview({ slots }: { slots: SlotResponse[] }) {
  if (slots.length === 0) {
    return <p className="text-sm text-ink-muted">No upcoming posts scheduled yet.</p>;
  }

  return (
    <ul className="flex flex-col gap-1.5 text-sm text-ink">
      {slots.map((slot) => {
        const label = statusLabel(slot.status);
        return (
          <li key={slot.id} className="flex items-center justify-between gap-3">
            <span>{formatSlot(slot.scheduled_at)}</span>
            {label ? <span className="text-xs text-ink-muted">{label}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}
