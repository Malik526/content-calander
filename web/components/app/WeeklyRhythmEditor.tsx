"use client";

import { useState } from "react";
import type { PostingTime } from "@/lib/api/types";

const WEEKDAYS: { key: string; label: string }[] = [
  { key: "monday", label: "Monday" },
  { key: "tuesday", label: "Tuesday" },
  { key: "wednesday", label: "Wednesday" },
  { key: "thursday", label: "Thursday" },
  { key: "friday", label: "Friday" },
  { key: "saturday", label: "Saturday" },
  { key: "sunday", label: "Sunday" },
];

const DEFAULT_NEW_TIME = "09:00";

function formatTimeLabel(time: string): string {
  const [hourStr, minuteStr] = time.split(":");
  const hour = Number(hourStr);
  if (Number.isNaN(hour) || minuteStr === undefined) return time;
  const period = hour >= 12 ? "PM" : "AM";
  const twelveHour = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelveHour}:${minuteStr.padStart(2, "0")} ${period}`;
}

/**
 * WeeklyRhythmEditor — the full-week posting-rhythm grid (Milestone 3.8.1,
 * replacing the one-weekday-at-a-time picker from 3.8). Fully controlled:
 * the caller owns the flat `postingTimes` list exactly as PUT /api/cadence
 * expects it (see lib/api/types.ts's PostingTime), and this component only
 * decides how a weekday-grouped view reads from and writes back to it —
 * it never calls the API itself.
 *
 * A day's on/off toggle is derived state (on iff it has at least one
 * posting time), not a separate stored flag: turning a day on seeds it
 * with one default time (09:00), turning it off drops every time for that
 * day. Adding a duplicate (weekday, time) pair is a no-op client-side —
 * posting_cadence_times has a real UNIQUE(cadence_id, weekday,
 * posting_time) constraint on the backend, and this is simpler than
 * surfacing that as a save-time error for a case the UI can just prevent.
 *
 * Milestone 3.14 UX cleanup: the always-visible draft time input + "Add
 * time" button (which caused confusion between persisted times and a
 * phantom default value of 09:00) are replaced by an explicit
 * "+ Add posting time" per-day trigger. Clicking it shows a time picker
 * and a confirm button inline; the value is committed only on confirm.
 * Persisted times appear as chips; nothing looks draft unless the user
 * deliberately opened the add form.
 */
export function WeeklyRhythmEditor({
  postingTimes,
  onChange,
}: {
  postingTimes: PostingTime[];
  onChange: (next: PostingTime[]) => void;
}) {
  const [addingForDay, setAddingForDay] = useState<string | null>(null);
  const [draftTime, setDraftTime] = useState(DEFAULT_NEW_TIME);

  function timesFor(weekday: string): PostingTime[] {
    return postingTimes
      .filter((entry) => entry.weekday === weekday)
      .slice()
      .sort((a, b) => a.posting_time.localeCompare(b.posting_time));
  }

  function handleToggleDay(weekday: string, enabled: boolean) {
    if (enabled) {
      onChange([...postingTimes, { weekday, posting_time: DEFAULT_NEW_TIME }]);
    } else {
      // Close any open add form for this day before removing its times.
      setAddingForDay((current) => (current === weekday ? null : current));
      onChange(postingTimes.filter((entry) => entry.weekday !== weekday));
    }
  }

  function handleStartAdding(weekday: string) {
    setAddingForDay(weekday);
    setDraftTime(DEFAULT_NEW_TIME);
  }

  function handleConfirmAdd(weekday: string) {
    const alreadyExists = postingTimes.some(
      (entry) => entry.weekday === weekday && entry.posting_time === draftTime,
    );
    if (!alreadyExists) {
      onChange([...postingTimes, { weekday, posting_time: draftTime }]);
    }
    setAddingForDay(null);
  }

  function handleCancelAdd() {
    setAddingForDay(null);
  }

  function handleRemoveTime(weekday: string, time: string) {
    onChange(postingTimes.filter((entry) => !(entry.weekday === weekday && entry.posting_time === time)));
  }

  return (
    <div className="flex flex-col divide-y divide-border">
      {WEEKDAYS.map(({ key, label }) => {
        const times = timesFor(key);
        const isActive = times.length > 0;
        const isAddingHere = addingForDay === key;

        return (
          <div key={key} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:gap-4">
            <label className="flex w-32 shrink-0 items-center gap-2 text-sm font-medium text-ink">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(event) => handleToggleDay(key, event.target.checked)}
              />
              {label}
            </label>

            {isActive ? (
              <div className="flex flex-1 flex-col gap-2">
                {/* Committed time chips — these are the real saved times. */}
                <div className="flex flex-wrap gap-2">
                  {times.map((entry) => (
                    <span
                      key={`${entry.weekday}-${entry.posting_time}`}
                      className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
                    >
                      <span>{formatTimeLabel(entry.posting_time)}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveTime(key, entry.posting_time)}
                        aria-label={`Remove ${label} ${formatTimeLabel(entry.posting_time)}`}
                        className="text-xs text-ink-muted hover:text-accent"
                      >
                        Remove
                      </button>
                    </span>
                  ))}
                </div>

                {/* Draft add form — only visible after clicking "+ Add posting time". */}
                {isAddingHere ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="time"
                      value={draftTime}
                      onChange={(event) => setDraftTime(event.target.value)}
                      aria-label={`New time for ${label}`}
                      className="rounded-lg border border-border bg-surface px-2 py-1.5 text-sm text-ink"
                    />
                    <button
                      type="button"
                      onClick={() => handleConfirmAdd(key)}
                      aria-label={`Add time to ${label}`}
                      className="inline-flex items-center justify-center rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
                    >
                      Add
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelAdd}
                      aria-label={`Cancel adding time to ${label}`}
                      className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-3 py-1.5 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleStartAdding(key)}
                    aria-label={`Add posting time for ${label}`}
                    className="self-start text-sm font-medium text-accent hover:underline"
                  >
                    + Add posting time
                  </button>
                )}
              </div>
            ) : (
              <p className="flex-1 text-sm text-ink-muted">Not posting</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
