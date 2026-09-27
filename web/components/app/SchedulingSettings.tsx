"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError } from "@/lib/api/client";
import { getCadence, getUpcomingSlots, saveCadence } from "@/lib/api/cadence";
import type { CadenceResponse, PostingTime, SlotResponse } from "@/lib/api/types";

// A short curated list, not a full IANA database — Milestone 3.8 keeps
// this minimal per its own brief ("keep styling minimal"); any real IANA
// name the backend accepts would work, this is just what the <select>
// offers directly.
const TIMEZONE_OPTIONS = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "UTC",
];

const WEEKDAY_OPTIONS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

/**
 * SchedulingSettings — Settings page's posting-cadence configuration
 * surface (Milestone 3.8). View/edit timezone, active days + times, save,
 * and preview the resulting upcoming content_slots. Deliberately minimal:
 * no drag/drop, no editing an individual generated slot, no calendar
 * view — those are 3.9's scope (queue/calendar editing UI), not this
 * milestone's.
 *
 * Save is one action end to end: PUT /api/cadence both persists the
 * config and regenerates the slot horizon atomically (see
 * api/routes/cadence.py), so this component just re-fetches the upcoming
 * slots right after a successful save rather than needing a separate
 * "generate" step.
 *
 * Props:
 *   accessToken — threaded through exactly like every other lib/api/*
 *     caller (see lib/api/platforms.ts), not read from useSession()
 *     itself.
 */
export function SchedulingSettings({ accessToken }: { accessToken: string | null }) {
  const [cadence, setCadence] = useState<CadenceResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [slots, setSlots] = useState<SlotResponse[]>([]);

  const [timezone, setTimezone] = useState(TIMEZONE_OPTIONS[0]);
  const [isActive, setIsActive] = useState(true);
  const [postingTimes, setPostingTimes] = useState<PostingTime[]>([]);
  const [newWeekday, setNewWeekday] = useState(WEEKDAY_OPTIONS[0]);
  const [newTime, setNewTime] = useState("09:00");

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function load() {
    setLoadError(null);
    try {
      const result = await getCadence(accessToken);
      setCadence(result);
      setTimezone(result.timezone ?? TIMEZONE_OPTIONS[0]);
      setIsActive(result.configured ? result.is_active : true);
      setPostingTimes(result.posting_times);
      if (result.configured) {
        const upcoming = await getUpcomingSlots(accessToken);
        setSlots(upcoming.slots);
      }
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : "Could not load your posting schedule.");
    }
  }

  useEffect(() => {
    if (!accessToken) return;
    // load is async — its setState calls happen after a real await, not
    // synchronously in this effect body — see settings/page.tsx's own
    // identical pattern/comment for loadStatus.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  function handleAddTime() {
    setPostingTimes((current) => [...current, { weekday: newWeekday, posting_time: newTime }]);
  }

  function handleRemoveTime(index: number) {
    setPostingTimes((current) => current.filter((_, i) => i !== index));
  }

  async function handleSave() {
    setSaveError(null);
    setSaving(true);
    try {
      const result = await saveCadence(accessToken, { timezone, is_active: isActive, posting_times: postingTimes });
      setCadence(result);
      const upcoming = await getUpcomingSlots(accessToken);
      setSlots(upcoming.slots);
    } catch (error) {
      setSaveError(error instanceof ApiError ? error.message : "Could not save your posting schedule.");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return <ErrorState message={loadError} onRetry={load} />;
  }

  if (cadence === null) {
    return (
      <Card>
        <Spinner label="Loading your posting schedule…" />
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-4">
      {saveError ? <ErrorState message={saveError} /> : null}

      <div className="flex flex-col gap-1">
        <label htmlFor="cadence-timezone" className="text-sm font-medium text-ink">
          Timezone
        </label>
        <select
          id="cadence-timezone"
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink"
        >
          {TIMEZONE_OPTIONS.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </div>

      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />
        Active
      </label>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-ink">Posting times</span>
        {postingTimes.length === 0 ? (
          <p className="text-sm text-ink-muted">No posting times set yet.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {postingTimes.map((entry, index) => (
              <li
                key={`${entry.weekday}-${entry.posting_time}-${index}`}
                className="flex items-center justify-between gap-3 text-sm text-ink"
              >
                <span className="capitalize">
                  {entry.weekday} {entry.posting_time}
                </span>
                <button
                  type="button"
                  onClick={() => handleRemoveTime(index)}
                  className="text-xs text-ink-muted hover:text-accent"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={newWeekday}
            onChange={(event) => setNewWeekday(event.target.value)}
            className="rounded-lg border border-border bg-surface px-2 py-1.5 text-sm capitalize text-ink"
          >
            {WEEKDAY_OPTIONS.map((day) => (
              <option key={day} value={day} className="capitalize">
                {day}
              </option>
            ))}
          </select>
          <input
            type="time"
            value={newTime}
            onChange={(event) => setNewTime(event.target.value)}
            className="rounded-lg border border-border bg-surface px-2 py-1.5 text-sm text-ink"
          />
          <Button type="button" variant="secondary" onClick={handleAddTime}>
            Add time
          </Button>
        </div>
      </div>

      <div>
        <Button type="button" onClick={() => void handleSave()} disabled={saving}>
          {saving ? "Saving…" : "Save schedule"}
        </Button>
      </div>

      {cadence.configured ? (
        <div className="flex flex-col gap-2 border-t border-border pt-4">
          <span className="text-sm font-medium text-ink">Upcoming slots</span>
          {slots.length === 0 ? (
            <p className="text-sm text-ink-muted">No upcoming slots generated yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm text-ink">
              {slots.map((slot) => (
                <li key={slot.id} className="flex justify-between gap-3">
                  <span>{slot.scheduled_at}</span>
                  <span className="text-ink-muted">{slot.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </Card>
  );
}
