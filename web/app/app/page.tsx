"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { getTikTokConnection } from "@/lib/api/platforms";
import { getCadence } from "@/lib/api/cadence";
import { listVideos } from "@/lib/api/videos";
import { connectedAccountName } from "@/lib/tiktokAccount";
import { ApiError } from "@/lib/api/client";
import { useSession } from "@/lib/session";
import type { TikTokConnectionStatus, CadenceResponse, VideoResponse } from "@/lib/api/types";

interface HomeData {
  tiktok: TikTokConnectionStatus;
  cadence: CadenceResponse;
  videos: VideoResponse[];
}

/**
 * /app — Activation / status home (Milestone 3.14 UX cleanup, replacing
 * the Milestone 3.5 quick-link shell). Shows real setup status using data
 * from existing APIs (no new backend state needed). A user who hasn't
 * configured anything sees what to do next with links to each section; a
 * fully-configured user sees a live summary of their pipeline.
 *
 * Graceful degradation: if the APIs fail or there's no accessToken (dev-
 * mock session), falls back to the old quick-link grid so the page is
 * never blank or broken.
 *
 * This is a usability baseline, not the final UI/UX design — a larger
 * professional redesign informed by real user feedback is future scope.
 */
export default function AppHomePage() {
  const { accessToken } = useSession();
  const [data, setData] = useState<HomeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!accessToken) {
      // No real backend to call — settle immediately to the fallback view
      // (quick-link grid) rather than spinning forever. Same reasoning as
      // lib/session.tsx's own null-accessToken handling in QueueScheduling
      // and library/page.tsx: setState called synchronously here is the
      // correct terminal state, not a cascading-render concern.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
      return;
    }
    void (async () => {
      try {
        const [tiktok, cadence, { videos }] = await Promise.all([
          getTikTokConnection(accessToken),
          getCadence(accessToken),
          listVideos(accessToken),
        ]);
        setData({ tiktok, cadence, videos });
      } catch (err) {
        // Any API error degrades to the fallback quick-link view — the
        // home page is not critical infrastructure, so a load failure
        // should never strand the user.
        if (!(err instanceof ApiError)) throw err;
      } finally {
        setLoading(false);
      }
    })();
  }, [accessToken]);

  if (loading) {
    return (
      <>
        <PageHeader title="Home" />
        <Card>
          <Spinner label="Loading…" />
        </Card>
      </>
    );
  }

  if (!data) {
    return (
      <>
        <PageHeader title="Home" description="Your posting workflow at a glance." />
        <div className="grid gap-4 sm:grid-cols-2">
          <QuickLinkCard href="/app/library" title="Library" description="Videos you've batched, ready to schedule." />
          <QuickLinkCard href="/app/queue" title="Queue" description="What's scheduled and what's already gone out." />
          <QuickLinkCard href="/app/settings" title="Settings" description="Your account and connected platforms." />
        </div>
      </>
    );
  }

  const { tiktok, cadence, videos } = data;
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
                <Link href={step.href} className="shrink-0 text-sm font-medium text-accent hover:underline">
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

function QuickLinkCard({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Link href={href} className="block">
      <Card className="h-full transition-colors hover:border-accent/40">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <p className="mt-1 text-sm text-ink-muted">{description}</p>
      </Card>
    </Link>
  );
}
