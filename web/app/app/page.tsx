"use client";

import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { useCadence } from "@/hooks/useCadence";
import { useTikTokConnection } from "@/hooks/useTikTokConnection";
import { useVideos } from "@/hooks/useVideos";
import { connectedAccountName } from "@/lib/tiktokAccount";

/**
 * /app — Activation / status home (Milestone 3.14 UX cleanup, replacing
 * the Milestone 3.5 quick-link shell). Shows real setup status using data
 * from existing APIs (no new backend state needed). A user who hasn't
 * configured anything sees what to do next with links to each section; a
 * fully-configured user sees a live summary of their pipeline.
 *
 * Milestone 3.15: reads the shared server-state cache (useTikTokConnection,
 * useCadence, useVideos — the same entries Settings, Queue and Library
 * use), so a revisit renders immediately from cache while stale entries
 * refresh in the background. The spinner shows only when nothing is
 * cached yet. A load failure is now shown with Retry instead of silently
 * falling back to quick links, which hid real errors.
 *
 * This is a usability baseline, not the final UI/UX design — a larger
 * professional redesign informed by real user feedback is future scope.
 */
export default function AppHomePage() {
  const tiktokQuery = useTikTokConnection();
  const cadenceQuery = useCadence();
  const videosQuery = useVideos();
  const queries = [tiktokQuery, cadenceQuery, videosQuery];

  const tiktok = tiktokQuery.data;
  const cadence = cadenceQuery.data;
  const videos = videosQuery.data;

  if (!tiktok || !cadence || !videos) {
    const failed = queries.find((query) => query.isError && query.data === undefined);
    return (
      <>
        <PageHeader title="Home" />
        {failed ? (
          <ErrorState
            message="Could not load your setup status."
            onRetry={() => queries.forEach((query) => void query.refetch())}
          />
        ) : (
          <Card>
            <Spinner label="Loading…" />
          </Card>
        )}
      </>
    );
  }

  const accountLabel = connectedAccountName(tiktok);
  const scheduledCount = videos.filter((v) => v.assigned_slot_id !== null).length;

  const setup = [
    {
      key: "tiktok",
      label: "Connect TikTok",
      done: tiktok.connected,
      doneDetail: accountLabel ?? "Connected",
      href: "/app/settings",
      cta: "Connect in Settings",
    },
    {
      key: "schedule",
      label: "Set up posting schedule",
      done: cadence.configured && cadence.is_active,
      doneDetail: cadence.timezone ?? "Enabled",
      href: "/app/queue",
      cta: "Set up in Queue",
    },
    {
      key: "videos",
      label: "Upload videos",
      done: videos.length > 0,
      doneDetail: videos.length === 1 ? "1 video" : `${videos.length} videos`,
      href: "/app/library",
      cta: "Upload in Library",
    },
    {
      key: "queue",
      label: "Add videos to your Queue",
      done: scheduledCount > 0,
      doneDetail: scheduledCount === 1 ? "1 scheduled" : `${scheduledCount} scheduled`,
      href: "/app/queue",
      cta: "Go to Queue",
    },
  ];

  const allDone = setup.every((s) => s.done);

  return (
    <>
      <PageHeader
        title="Home"
        description={allDone ? "Your posting pipeline is active." : "Get Pickle Batch ready."}
      />
      <Card>
        <ul className="flex flex-col divide-y divide-border">
          {setup.map((step) => (
            <li key={step.key} className="flex min-h-12 items-center gap-3 py-3 first:pt-0 last:pb-0">
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  step.done
                    ? "bg-status-success-soft text-status-success"
                    : "border border-border bg-surface text-ink-muted"
                }`}
                aria-hidden="true"
              >
                {step.done ? "✓" : ""}
              </span>
              <span className={`flex-1 text-sm ${step.done ? "font-medium text-ink" : "text-ink-muted"}`}>
                {step.label}
              </span>
              {step.done ? (
                <span className="shrink-0 text-sm text-ink-muted">{step.doneDetail}</span>
              ) : (
                <Link href={step.href} className="tap-target shrink-0 text-sm font-medium text-accent hover:underline">
                  {step.cta} →
                </Link>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
