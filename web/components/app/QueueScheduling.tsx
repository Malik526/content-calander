"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { UpcomingSchedulePreview } from "@/components/app/UpcomingSchedulePreview";
import { WeeklyRhythmEditor } from "@/components/app/WeeklyRhythmEditor";
import { ApiError } from "@/lib/api/client";
import { getCadence, getUpcomingSlots, saveCadence } from "@/lib/api/cadence";
import type { CadenceResponse, PostingTime, SlotResponse } from "@/lib/api/types";

// A short curated list, not a full IANA database — Milestone 3.8 kept this
// minimal per its own brief ("keep styling minimal"); any real IANA name
// the backend accepts would work, this is just what the <select> offers.
const TIMEZONE_OPTIONS = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "UTC",
];

/**
 * QueueScheduling — Queue's "Posting rhythm" + "Upcoming schedule"
 * sections (Milestone 3.8.1). Replaces components/app/SchedulingSettings.tsx,
 * which lived on the Settings page and exposed the cadence editor as a
 * one-time-at-a-time list plus a raw ISO/status dump — both replaced here
 * with WeeklyRhythmEditor (the full-week grid) and UpcomingSchedulePreview
 * (human-readable dates). Settings no longer has its own scheduling editor
 * at all, so this is the single source of truth for the cadence config —
 * see docs/decisions/0012-hosted-cadence-configuration.md, unchanged by
 * this UX pass.
 *
 * Save is still one action end to end: PUT /api/cadence both persists the
 * config and regenerates the slot horizon atomically (api/routes/cadence.py),
 * so this component just re-fetches the upcoming slots right after a
 * successful save.
 *
 * Props:
 *   accessToken — threaded through exactly like every other lib/api/*
 *     caller (see lib/api/platforms.ts), not read from useSession() itself.
 */
export function QueueScheduling({ accessToken }: { accessToken: string | null }) {
  const [cadence, setCadence] = useState<CadenceResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [slots, setSlots] = useState<SlotResponse[]>([]);

  const [timezone, setTimezone] = useState(TIMEZONE_OPTIONS[0]);
  const [isActive, setIsActive] = useState(true);
  const [postingTimes, setPostingTimes] = useState<PostingTime[]>([]);

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
    if (!accessToken) {
      // No real backend to call at all in this case (dev-mock session —
      // see lib/session.tsx and library/page.tsx's identical handling) —
      // settling straight to "not configured" is the correct terminal
      // state here, not an indefinite spinner.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCadence({ configured: false, timezone: null, is_active: false, posting_times: [] });
      return;
    }
    // load is async — its setState calls happen after a real await, not
    // synchronously in this effect body — see settings/page.tsx's own
    // identical pattern/comment for loadStatus.
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

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
    <div className="flex flex-col gap-6">
      <section aria-labelledby="posting-rhythm-heading">
        <h2 id="posting-rhythm-heading" className="mb-3 text-sm font-semibold text-ink">
          Posting rhythm
        </h2>
        <Card className="flex flex-col gap-4">
          {saveError ? <ErrorState message={saveError} /> : null}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
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
          </div>

          <WeeklyRhythmEditor postingTimes={postingTimes} onChange={setPostingTimes} />

          <div>
            <Button type="button" onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Saving…" : "Save schedule"}
            </Button>
          </div>
        </Card>
      </section>

      <section aria-labelledby="upcoming-schedule-heading">
        <h2 id="upcoming-schedule-heading" className="mb-3 text-sm font-semibold text-ink">
          Upcoming schedule
        </h2>
        <Card>
          <UpcomingSchedulePreview slots={slots} />
        </Card>
      </section>
    </div>
  );
}
