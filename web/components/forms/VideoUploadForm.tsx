"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useUploadManager } from "@/lib/uploads";

/**
 * VideoUploadForm — select multiple finished video files and upload them
 * as one real batch request (Milestone 3.7). Select and upload are two
 * distinct steps (Phase 2's own step 1/2 split): picking files only stages
 * them locally; nothing is sent until "Upload" is clicked, so a wrong
 * pick can be cleared first.
 *
 * The actual in-progress/result state (`uploading`/`lastResults`/`error`)
 * comes from useUploadManager() (lib/uploads.tsx), not local state — that
 * shared state lives above this component in app/app/layout.tsx and
 * survives this component unmounting if the user navigates elsewhere
 * mid-upload (Milestone 3.7 follow-up). Only the *pending selection*
 * (`selectedFiles`, before "Upload" is clicked) stays local — losing an
 * unsubmitted pick on navigation is fine; nothing was sent yet.
 *
 * Props:
 *   accessToken — threaded through exactly like every other lib/api/*
 *     caller (see lib/api/platforms.ts) rather than reading useSession()
 *     itself, so this component stays testable/reusable without a real
 *     session provider.
 */
export function VideoUploadForm({ accessToken }: { accessToken: string | null }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const { uploading, lastResults, error, upload } = useUploadManager();

  function handleFilesSelected(fileList: FileList | null) {
    setSelectedFiles(fileList ? Array.from(fileList) : []);
  }

  function handleClear() {
    setSelectedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleUpload() {
    if (selectedFiles.length === 0) return;
    const filesToUpload = selectedFiles;
    setSelectedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
    await upload(filesToUpload, accessToken);
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

      {error ? <p className="text-sm font-medium text-status-danger">{error}</p> : null}

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

      {!uploading && lastResults ? (
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
