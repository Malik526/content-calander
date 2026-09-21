/**
 * mockData.ts — static shell data (Milestone 3.5).
 *
 * Every product page ships empty by default (see the pages themselves —
 * they render EmptyState, not this data, unless a page explicitly opts
 * into a "populated" preview) so the shell's real, shipped behavior is
 * "no videos yet" / "nothing scheduled yet" / "no platform connected
 * yet" — not a permanently-populated demo. This file exists so the shell
 * can still demonstrate populated-state layout during development
 * without a real backend; nothing here is wired to run against it by
 * default. Never imported by lib/api/client.ts — this is explicitly the
 * pre-API stand-in Phase 12 allows, not a fallback the real client
 * degrades to.
 */

import type { PlatformConnectionSummary, QueueItem, VideoSummary } from "@/lib/api/types";

export const mockVideos: VideoSummary[] = [
  {
    id: "v1",
    title: "Behind the scenes — building the scheduler",
    status: "ready",
    durationSeconds: 42,
    createdAt: "2026-09-18T14:00:00Z",
  },
  {
    id: "v2",
    title: "What I learned shipping to production",
    status: "processing",
    durationSeconds: null,
    createdAt: "2026-09-19T09:30:00Z",
  },
];

export const mockQueue: QueueItem[] = [
  {
    id: "q1",
    videoTitle: "Behind the scenes — building the scheduler",
    platform: "tiktok",
    status: "pending",
    scheduledAt: "2026-09-22T13:00:00Z",
  },
  {
    id: "q2",
    videoTitle: "Why I switched routing strategies",
    platform: "tiktok",
    status: "published",
    scheduledAt: "2026-09-17T13:00:00Z",
  },
];

export const mockPlatformConnections: PlatformConnectionSummary[] = [
  { id: "c1", platform: "tiktok", accountLabel: null, status: "disconnected" },
];
