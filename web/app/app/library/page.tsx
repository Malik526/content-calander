"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { VideoUploadForm } from "@/components/forms/VideoUploadForm";
import { ApiError } from "@/lib/api/client";
import { deleteVideo, listVideos } from "@/lib/api/videos";
import type { VideoResponse } from "@/lib/api/types";
import { useSession } from "@/lib/session";
import { libraryFilterFor, libraryPublishStatus, presentQueueStatus, type LibraryFilter } from "@/lib/status";
import { useUploadManager } from "@/lib/uploads";

const FILTER_TABS: { key: LibraryFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unscheduled", label: "Unscheduled" },
  { key: "scheduled", label: "Scheduled" },
  { key: "published", label: "Published" },
];

const EMPTY_FILTER_COPY: Record<Exclude<LibraryFilter, "all">, { title: string; description: string }> = {
  unscheduled: { title: "No unscheduled videos", description: "All your videos are already in the queue." },
  scheduled: { title: "No scheduled videos", description: "Head to Queue to schedule a video." },
  published: { title: "No published videos yet", description: "Videos appear here once they've been posted." },
};

/**
 * /app/library (Milestone 3.7). Real batch upload + real, backend-verified
 * video listing — replaces the Milestone 3.5 static "coming soon" shell.
 * Every video shown here is the authenticated user's own
 * (GET /api/videos, scoped server-side — see api/routes/videos.py); this
 * page never filters client-side, since the backend never returns another
 * user's rows in the first place.
 *
 * No accessToken (the dev-mock-session fallback, see lib/session.tsx) means
 * there is no real backend to call at all — shown as the same empty state
 * as "no videos yet" rather than spinning forever, since that's the
 * honest outcome either way.
 *
 * Milestone 3.7 follow-up: refreshing after an upload is driven by
 * useUploadManager()'s shared `uploading` flag (lib/uploads.tsx), not a
 * callback from VideoUploadForm — that state lives above this page in the
 * app shell layout and survives this page unmounting, so a batch that
 * finishes while the user is on Queue/Settings is picked up by this
 * page's own mount-time fetch when they come back; the effect below only
 * has to handle the case where this page is still mounted when a batch
 * completes.
 *
 * Milestone 3.7 follow-up: each row also has a Delete action (inline
 * confirm, no modal) — DELETE /api/videos/{id}. The backend refuses
 * (409) a video that's still assigned to a scheduled slot or has a
 * platform post; that error surfaces here as plain text exactly as the
 * backend phrased it, telling the user to cancel/remove those entries
 * first rather than this page attempting to do that for them.
 *
 * Milestone 3.14 UX cleanup: filter tabs (All / Unscheduled / Scheduled)
 * derived from videos.assigned_slot_id — no backend change needed. A
 * "Scheduled" badge on each row shows at a glance which videos are already
 * in the queue. Filter state is client-local; the full list is always
 * fetched from the server.
 *
 * Milestone 3.14 final follow-up: tabs and badges now come from the
 * backend's publish_status (the same resolver the Queue uses) instead of
 * assigned_slot_id, adding a Published tab. Scheduled covers every
 * not-yet-published state; the badge uses the Queue's own labels.
 */
export default function LibraryPage() {
  const { accessToken } = useSession();
  const { uploading } = useUploadManager();
  const [videos, setVideos] = useState<VideoResponse[] | null>(null);
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function loadVideos() {
    setLoadError(null);
    try {
      const { videos: loaded } = await listVideos(accessToken);
      // Dev-only: the libraryPublishStatus fallback hides an API that predates
      // publish_status (e.g. localhost pointed at an older deployed backend).
      if (process.env.NODE_ENV !== "production" && loaded.some((video) => video.publish_status === undefined)) {
        console.warn(
          "Library: /api/videos returned no publish_status, so statuses fall back to assigned_slot_id " +
            "(published videos show as Scheduled). The API at NEXT_PUBLIC_API_BASE_URL is older than this frontend.",
        );
      }
      setVideos(loaded);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : "Could not load your videos.");
    }
  }

  useEffect(() => {
    if (!accessToken) {
      // No real backend to call at all in this case (see this component's
      // own doc comment) — settling straight to "no videos" is the
      // correct terminal state here, not a synchronization step a
      // cleanup/subscription would otherwise handle.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setVideos([]);
      return;
    }
    void loadVideos();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  const wasUploadingRef = useRef(uploading);
  useEffect(() => {
    if (wasUploadingRef.current && !uploading) {
      void loadVideos(); // a batch just finished while this page was mounted
    }
    wasUploadingRef.current = uploading;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploading]);

  /**
   * Delete Video (Milestone 3.7 follow-up). Confirmation is a two-click
   * inline affordance (see the row's own render below) rather than a
   * modal dialog — no Dialog/Modal primitive exists in this shell yet,
   * and this milestone's own scope guardrail is a small delete action,
   * not a new UI primitive. On success, re-fetches from the backend
   * rather than optimistically splicing the deleted row out locally, so
   * the list always reflects real server state (same reasoning as the
   * uploading-transition refresh above).
   */
  async function handleDelete(videoId: number) {
    setDeleteError(null);
    setDeletingId(videoId);
    try {
      await deleteVideo(accessToken, videoId);
      setConfirmingDeleteId(null);
      await loadVideos();
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Could not delete this video.");
    } finally {
      setDeletingId(null);
    }
  }

  // Derive counts from the full list for filter tab labels; apply filter for display.
  const matchesFilter = (video: VideoResponse, tab: LibraryFilter) =>
    tab === "all" || libraryFilterFor(libraryPublishStatus(video)) === tab;

  const counts = Object.fromEntries(
    FILTER_TABS.map(({ key }) => [key, videos?.filter((v) => matchesFilter(v, key)).length ?? 0]),
  ) as Record<LibraryFilter, number>;

  const filteredVideos = videos === null ? null : videos.filter((video) => matchesFilter(video, filter));

  return (
    <>
      <PageHeader title="Library" description="Videos you've batched and processed." />
      <div className="flex flex-col gap-6">
        <VideoUploadForm accessToken={accessToken} />

        {deleteError ? <p className="text-sm font-medium text-status-danger">{deleteError}</p> : null}

        {loadError ? (
          <ErrorState message={loadError} onRetry={loadVideos} />
        ) : videos === null ? (
          <Card>
            <Spinner label="Loading your videos…" />
          </Card>
        ) : videos.length === 0 ? (
          <EmptyState title="No videos yet" description="Upload a video above to see it here." />
        ) : (
          <>
            {/* Filter tabs — purely client-side, no extra API calls. */}
            <div className="flex gap-1" role="tablist" aria-label="Filter videos">
              {FILTER_TABS.map(({ key: tab, label }) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={filter === tab}
                  onClick={() => setFilter(tab)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                    filter === tab
                      ? "bg-accent-soft text-accent"
                      : "text-ink-muted hover:bg-background hover:text-ink"
                  }`}
                >
                  {label}
                  <span className="ml-1.5 text-xs opacity-70">({counts[tab]})</span>
                </button>
              ))}
            </div>

            {filter !== "all" && filteredVideos !== null && filteredVideos.length === 0 ? (
              <EmptyState title={EMPTY_FILTER_COPY[filter].title} description={EMPTY_FILTER_COPY[filter].description} />
            ) : (
              <ul className="flex flex-col gap-3">
                {(filteredVideos ?? []).map((video) => (
                  <li key={video.id}>
                    <Card className="flex items-center gap-3 sm:gap-4">
                      <div className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                        <p className="truncate text-sm font-medium text-ink">{video.original_filename}</p>
                        <LibraryStatusBadge publishStatus={libraryPublishStatus(video)} />
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        <p className="hidden text-xs text-ink-muted sm:block">{formatFileSize(video.file_size_bytes)}</p>
                        {confirmingDeleteId === video.id ? (
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void handleDelete(video.id)}
                              disabled={deletingId === video.id}
                              className="text-xs font-medium text-status-danger hover:underline disabled:opacity-60"
                            >
                              {deletingId === video.id ? "Deleting…" : "Confirm delete"}
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmingDeleteId(null)}
                              disabled={deletingId === video.id}
                              className="text-xs font-medium text-ink-muted hover:text-ink disabled:opacity-60"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setConfirmingDeleteId(video.id)}
                            className="text-xs font-medium text-ink-muted hover:text-status-danger"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </>
  );
}

function formatFileSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Queue-language badge for a video's publish status; none when unscheduled. */
function LibraryStatusBadge({ publishStatus }: { publishStatus: string }) {
  if (publishStatus === "UNSCHEDULED") return null;
  const { label, tone } = presentQueueStatus(publishStatus);
  return <Badge tone={tone}>{label}</Badge>;
}
