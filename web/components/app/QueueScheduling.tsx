"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { WeeklyRhythmEditor } from "@/components/app/WeeklyRhythmEditor";
import { ApiError } from "@/lib/api/client";
import { getCadence, saveCadence } from "@/lib/api/cadence";
import type { CadenceResponse, PostingTime } from "@/lib/api/types";

// A short curated list, not a full IANA database — Milestone 3.8 kept this
// minimal per its own brief ("keep styling minimal"); any real IANA name
// the backend accepts would work, this is just what the <select> offers.
// Milestone 3.14: the browser-detected timezone is appended dynamically if
// not already present so a new user's default is never missing from the list.
const TIMEZONE_OPTIONS = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "UTC",
];

/** Returns the browser's own IANA timezone, falling back to the first
 * option in the curated list when unavailable (SSR, unusual environment). */
function getDetectedTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || TIMEZONE_OPTIONS[0];
  } catch {
    return TIMEZONE_OPTIONS[0];
  }
}

/**
 * QueueScheduling — Queue's "Posting rhythm" section: view/edit the
 * recurring cadence that defines future OPEN slot capacity (Milestone 3.8,
 * rebuilt as a full week grid in 3.8.1). Milestone 3.9 (Queue + Calendar
 * Functionality) removed this component's own "Upcoming schedule" preview
 * — what actually occupies that capacity is now components/app/QueueBoard.tsx's
 * job (real content_slots with assigned videos/publish status, not just a
 * bare upcoming-slots list), per this milestone's own framing: "Cadence
 * defines future OPEN capacity. Queue/Calendar shows what actually
 * occupies that capacity." GET /api/cadence/slots itself is untouched and
 * still used by GET /api/queue/slots's own read path indirectly (both
 * ultimately read ContentStoreProtocol.list_content_slots_for_user), but
 * this component no longer calls it directly.
 *
 * Save is still one action end to end: PUT /api/cadence both persists the
 * config and regenerates the slot horizon atomically
 * (api/routes/cadence.py). Milestone 3.14: onSaved then fires so the page
 * can reload QueueBoard, which shows those regenerated slots.
 *
 * Milestone 3.14 UX cleanup:
 *   "Active" checkbox label → "Posting schedule enabled" with supporting
 *   copy, so the control's purpose is unambiguous.
 *   Timezone default → browser-detected IANA timezone for new users (no
 *   saved timezone); an existing saved timezone always takes priority.
 *
 * Props:
 *   accessToken — threaded through exactly like every other lib/api/*
 *     caller (see lib/api/platforms.ts), not read from useSession() itself.
 *   onSaved — called after a successful save (not after a failed one).
 */
export function QueueScheduling({
  accessToken,
  onSaved,
}: {
  accessToken: string | null;
  onSaved?: () => void;
}) {
  const [cadence, setCadence] = useState<CadenceResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [timezone, setTimezone] = useState(TIMEZONE_OPTIONS[0]);
  const [isActive, setIsActive] = useState(true);
  const [postingTimes, setPostingTimes] = useState<PostingTime[]>([]);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Always include the current timezone value in the select options — if the
  // browser-detected or previously-saved timezone isn't in the curated list
  // (e.g. "Europe/Berlin"), add it rather than showing a broken select.
  const timezoneOptions = useMemo(() => {
    if (TIMEZONE_OPTIONS.includes(timezone)) return TIMEZONE_OPTIONS;
    return [...TIMEZONE_OPTIONS, timezone];
  }, [timezone]);

  async function load() {
    setLoadError(null);
    try {
      const result = await getCadence(accessToken);
      setCadence(result);
      // Use the saved timezone when present; fall back to the browser-detected
      // IANA timezone so a new user sees a sensible default without having to
      // know the right string. An existing saved value is never overwritten.
      setTimezone(result.timezone ?? getDetectedTimezone());
      setIsActive(result.configured ? result.is_active : true);
      setPostingTimes(result.posting_times);
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
      onSaved?.();
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

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
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
            {timezoneOptions.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="flex items-center gap-2 text-sm font-medium text-ink">
            <input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />
            Posting schedule enabled
          </label>
          <p className="ml-5 text-xs text-ink-muted">
            When enabled, Pickle Batch creates future posting slots from this schedule.
          </p>
        </div>
      </div>

      <WeeklyRhythmEditor postingTimes={postingTimes} onChange={setPostingTimes} />

      <div>
        <Button type="button" onClick={() => void handleSave()} disabled={saving}>
          {saving ? "Saving…" : "Save schedule"}
        </Button>
      </div>
    </Card>
  );
}
