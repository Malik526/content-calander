"use client";

import { useEffect, useRef, useState } from "react";
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
import { useUploadManager } from "@/lib/uploads";

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
 */
export default function LibraryPage() {
  const { accessToken } = useSession();
  const { uploading } = useUploadManager();
  const [videos, setVideos] = useState<VideoResponse[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function loadVideos() {
    setLoadError(null);
    try {
      const { videos } = await listVideos(accessToken);
      setVideos(videos);
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
          <EmptyState
            title="No videos yet"
            description="Upload a video above to see it here."
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {videos.map((video) => (
              <li key={video.id}>
                <Card className="flex items-center justify-between gap-4">
                  <p className="truncate text-sm font-medium text-ink">{video.original_filename}</p>
                  <div className="flex shrink-0 items-center gap-3">
                    <p className="text-xs text-ink-muted">{formatFileSize(video.file_size_bytes)}</p>
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
      </div>
    </>
  );
}

function formatFileSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
