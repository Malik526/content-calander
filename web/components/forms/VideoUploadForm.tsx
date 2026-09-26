"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api/client";
import { uploadVideos } from "@/lib/api/videos";
import type { VideoUploadResult } from "@/lib/api/types";

/**
 * VideoUploadForm — select multiple finished video files and upload them
 * as one real batch request (Milestone 3.7). Select and upload are two
 * distinct steps (Phase 2's own step 1/2 split): picking files only stages
 * them locally; nothing is sent until "Upload" is clicked, so a wrong
 * pick can be cleared first.
 *
 * Props:
 *   accessToken — threaded through exactly like every other lib/api/*
 *     caller (see lib/api/platforms.ts) rather than reading useSession()
 *     itself, so this component stays testable/reusable without a real
 *     session provider.
 *   onUploaded — called with the batch's results once the request
 *     completes (success or partial failure) so the caller (Library page)
 *     can refresh its video list; never called on a request-level failure
 *     (network/auth error), which is shown inline here instead.
 */
export function VideoUploadForm({
  accessToken,
  onUploaded,
}: {
  accessToken: string | null;
  onUploaded: (results: VideoUploadResult[]) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [lastResults, setLastResults] = useState<VideoUploadResult[] | null>(null);

  function handleFilesSelected(fileList: FileList | null) {
    setLastResults(null);
    setRequestError(null);
    setSelectedFiles(fileList ? Array.from(fileList) : []);
  }

  function handleClear() {
    setSelectedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleUpload() {
    if (selectedFiles.length === 0) return;
    setUploading(true);
    setRequestError(null);
    try {
      const { results } = await uploadVideos(accessToken, selectedFiles);
      setLastResults(results);
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onUploaded(results);
    } catch (error) {
      setRequestError(error instanceof ApiError ? error.message : "Could not upload your videos.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <label htmlFor="video-upload-input" className="text-sm font-medium text-ink">
          Upload videos
        </label>
        <input
          id="video-upload-input"
          ref={fileInputRef}
          type="file"
          multiple
          accept="video/mp4,video/quicktime,.mp4,.mov"
          onChange={(e) => handleFilesSelected(e.target.files)}
          disabled={uploading}
          className="text-sm text-ink-muted file:mr-3 file:rounded-lg file:border file:border-border file:bg-surface file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:border-accent/40"
        />
        {selectedFiles.length > 0 ? (
          <p className="text-xs text-ink-muted">
            {selectedFiles.length} file{selectedFiles.length === 1 ? "" : "s"} selected: {selectedFiles.map((f) => f.name).join(", ")}
          </p>
        ) : null}
      </div>

      {requestError ? <p className="text-sm font-medium text-status-danger">{requestError}</p> : null}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          onClick={() => void handleUpload()}
          disabled={selectedFiles.length === 0 || uploading}
        >
          {uploading ? "Uploading…" : `Upload ${selectedFiles.length || ""} video${selectedFiles.length === 1 ? "" : "s"}`.trim()}
        </Button>
        {selectedFiles.length > 0 && !uploading ? (
          <Button type="button" variant="secondary" onClick={handleClear}>
            Clear
          </Button>
        ) : null}
      </div>

      {lastResults ? (
        <ul className="flex flex-col gap-1 text-sm">
          {lastResults.map((result, i) => (
            <li key={`${result.filename}-${i}`} className={result.success ? "text-status-success" : "text-status-danger"}>
              {result.success ? "✓" : "✗"} {result.filename}
              {result.error ? ` — ${result.error}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
