"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { VideoUploadForm } from "@/components/forms/VideoUploadForm";
import { ApiError } from "@/lib/api/client";
import { listVideos } from "@/lib/api/videos";
import type { VideoResponse } from "@/lib/api/types";
import { useSession } from "@/lib/session";

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
 */
export default function LibraryPage() {
  const { accessToken } = useSession();
  const [videos, setVideos] = useState<VideoResponse[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

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

  return (
    <>
      <PageHeader title="Library" description="Videos you've batched and processed." />
      <div className="flex flex-col gap-6">
        <VideoUploadForm accessToken={accessToken} onUploaded={() => void loadVideos()} />

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
                  <p className="shrink-0 text-xs text-ink-muted">{formatFileSize(video.file_size_bytes)}</p>
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
