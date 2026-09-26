"use client";

/**
 * uploads.tsx — the one place an in-progress batch upload's state lives
 * (Milestone 3.7 follow-up — navigation/upload lifecycle review).
 *
 * Before this: VideoUploadForm owned `uploading`/`lastResults`/`error` as
 * its own local useState. That's fine for the request itself — a real
 * `fetch()` call isn't tied to React's component tree and keeps running
 * regardless of what unmounts — but the *feedback* (is something still
 * uploading? what were the results?) was lost the moment the user
 * navigated to Queue or Settings and back, since VideoUploadForm/
 * LibraryPage remount fresh on return. That's the real problem this
 * milestone's own brief is pointing at ("is upload state owned only by
 * the page component?") — not that navigation cancels the request, but
 * that navigating away and back looks like the upload silently vanished.
 *
 * UploadProvider lives in app/app/layout.tsx, above the per-route page
 * content — exactly like SessionProvider already does — so it persists
 * across every /app/* navigation. VideoUploadForm/LibraryPage now read
 * this shared state via useUploadManager() instead of owning their own.
 *
 * Deliberately does not attempt browser-close/tab-close/resumable upload
 * support — out of this milestone's scope. Only in-app route navigation
 * is addressed.
 */

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api/client";
import { uploadVideos } from "@/lib/api/videos";
import type { VideoUploadResult } from "@/lib/api/types";

export interface UploadManager {
  uploading: boolean;
  lastResults: VideoUploadResult[] | null;
  error: string | null;
  upload: (files: FileList | File[], accessToken: string | null) => Promise<void>;
}

const UploadContext = createContext<UploadManager | null>(null);

export function UploadProvider({ children }: { children: ReactNode }) {
  const [uploading, setUploading] = useState(false);
  const [lastResults, setLastResults] = useState<VideoUploadResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = useCallback(async (files: FileList | File[], accessToken: string | null) => {
    setUploading(true);
    setError(null);
    try {
      const { results } = await uploadVideos(accessToken, files);
      setLastResults(results);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not upload your videos.");
    } finally {
      setUploading(false);
    }
  }, []);

  return <UploadContext.Provider value={{ uploading, lastResults, error, upload }}>{children}</UploadContext.Provider>;
}

export function useUploadManager(): UploadManager {
  const context = useContext(UploadContext);
  if (!context) {
    throw new Error("useUploadManager must be used within an UploadProvider (see app/app/layout.tsx).");
  }
  return context;
}
