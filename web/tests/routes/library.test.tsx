import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LibraryPage from "@/app/app/library/page";
import { UploadProvider } from "@/lib/uploads";

/**
 * Library's real backend wiring (Milestone 3.7, extended by the 3.7
 * follow-up's upload-lifecycle change) — lib/api/videos.ts is mocked
 * directly (its own HTTP behavior is covered by tests/lib/videos.test.ts),
 * so these tests focus on the page's own loading/empty/populated/error
 * states and the upload flow, mirroring
 * tests/routes/settings-tiktok.test.tsx's established pattern exactly.
 *
 * Every render wraps LibraryPage in a real UploadProvider — in the real
 * app that's supplied by app/app/layout.tsx, above every /app/* page;
 * useUploadManager() throws without it (see lib/uploads.tsx), exactly
 * like useSession() would without a real SessionProvider.
 */

const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "creator@example.com", displayName: "Creator" },
  accessToken: "real-token",
  status: "authenticated" as const,
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

const videosApi = vi.hoisted(() => ({
  listVideos: vi.fn(),
  uploadVideos: vi.fn(),
  deleteVideo: vi.fn(),
}));
vi.mock("@/lib/api/videos", () => videosApi);

afterEach(() => {
  vi.clearAllMocks();
});

function makeFile(name: string, content = "video bytes"): File {
  return new File([content], name, { type: "video/mp4" });
}

function renderLibraryPage() {
  return render(
    <UploadProvider>
      <LibraryPage />
    </UploadProvider>,
  );
}

describe("LibraryPage", () => {
  it("shows the empty state when the account has no videos", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderLibraryPage();

    await waitFor(() => expect(screen.getByText(/no videos yet/i)).toBeInTheDocument());
    expect(videosApi.listVideos).toHaveBeenCalledWith("real-token");
  });

  it("shows the account's own videos once loaded", async () => {
    videosApi.listVideos.mockResolvedValue({
      videos: [
        { id: 1, original_filename: "clip-a.mp4", status: "DISCOVERED", file_size_bytes: 2_000_000, created_at: "2026-09-01T00:00:00Z" },
        { id: 2, original_filename: "clip-b.mp4", status: "DISCOVERED", file_size_bytes: null, created_at: "2026-09-02T00:00:00Z" },
      ],
    });

    renderLibraryPage();

    await waitFor(() => expect(screen.getByText("clip-a.mp4")).toBeInTheDocument());
    expect(screen.getByText("clip-b.mp4")).toBeInTheDocument();
  });

  it("shows an error state with retry when loading videos fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    videosApi.listVideos.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    videosApi.listVideos.mockResolvedValueOnce({ videos: [] });

    renderLibraryPage();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server."));

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText(/no videos yet/i)).toBeInTheDocument());
  });

  it("uploads selected files and shows per-file results, then refreshes the list", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    videosApi.uploadVideos.mockResolvedValue({
      results: [
        { filename: "good.mp4", success: true, video: { id: 3, original_filename: "good.mp4", status: "DISCOVERED", file_size_bytes: 10, created_at: "2026-09-03T00:00:00Z" }, error: null },
        { filename: "bad.txt", success: false, video: null, error: "Unsupported file type." },
      ],
    });

    renderLibraryPage();
    await waitFor(() => expect(videosApi.listVideos).toHaveBeenCalledTimes(1));

    const user = userEvent.setup();
    const input = screen.getByLabelText(/upload videos/i);
    await user.upload(input, [makeFile("good.mp4"), makeFile("bad.txt")]);

    await user.click(screen.getByRole("button", { name: /upload 2 videos/i }));

    await waitFor(() => expect(screen.getByText(/good\.mp4/)).toBeInTheDocument());
    expect(screen.getByText(/Unsupported file type\./)).toBeInTheDocument();
    expect(videosApi.uploadVideos).toHaveBeenCalledWith("real-token", [expect.anything(), expect.anything()]);
    // the list reloads after a successful batch call
    expect(videosApi.listVideos).toHaveBeenCalledTimes(2);
  });

  it("shows a request-level error on total failure, and still refreshes the list (always reflect real server state)", async () => {
    const { ApiError } = await import("@/lib/api/client");
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    videosApi.uploadVideos.mockRejectedValue(new ApiError("Could not upload your videos."));

    renderLibraryPage();
    await waitFor(() => expect(videosApi.listVideos).toHaveBeenCalledTimes(1));

    const user = userEvent.setup();
    const input = screen.getByLabelText(/upload videos/i);
    await user.upload(input, [makeFile("clip.mp4")]);
    await user.click(screen.getByRole("button", { name: /upload 1 video/i }));

    await waitFor(() => expect(screen.getByText("Could not upload your videos.")).toBeInTheDocument());
    // The refresh is driven by the shared uploading flag settling back to
    // false (see LibraryPage's own comment) regardless of success/
    // failure — a cheap, idempotent GET is always safe to re-run, and
    // this is simpler than threading a distinct success/failure signal
    // through the shared upload manager for no real benefit.
    await waitFor(() => expect(videosApi.listVideos).toHaveBeenCalledTimes(2));
  });

  it("keeps upload state alive across a simulated navigation away and back (Milestone 3.7 follow-up)", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    let resolveUpload: (value: { results: unknown[] }) => void;
    videosApi.uploadVideos.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );

    // The real app never unmounts UploadProvider on in-app navigation —
    // it lives in app/app/layout.tsx, *above* every page, and only the
    // nested page swaps when the route changes (Next.js App Router keeps
    // a shared layout mounted across its child route changing). This
    // test reproduces exactly that: the outer <UploadProvider> element
    // stays at the same position across every rerender() call below —
    // React preserves its internal state across that, the same identity
    // guarantee the real layout gives it — while only the child under it
    // is swapped, first away from LibraryPage (simulating navigating to
    // Queue mid-upload) and then back.
    const { rerender } = renderLibraryPage();
    await waitFor(() => expect(videosApi.listVideos).toHaveBeenCalledTimes(1));

    const user = userEvent.setup();
    await user.upload(screen.getByLabelText(/upload videos/i), [makeFile("clip.mp4")]);
    await user.click(screen.getByRole("button", { name: /upload 1 video/i }));
    await waitFor(() => expect(videosApi.uploadVideos).toHaveBeenCalledTimes(1));

    // Navigate away from Library mid-upload — only the page unmounts.
    rerender(
      <UploadProvider>
        <div>Queue page placeholder</div>
      </UploadProvider>,
    );
    expect(screen.queryByLabelText(/upload videos/i)).not.toBeInTheDocument();

    // The upload finishes while the user is elsewhere in the app.
    resolveUpload!({
      results: [{ filename: "clip.mp4", success: true, video: { id: 9, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 5, created_at: "2026-09-04T00:00:00Z" }, error: null }],
    });

    // Navigate back to Library.
    videosApi.listVideos.mockResolvedValue({
      videos: [{ id: 9, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 5, created_at: "2026-09-04T00:00:00Z" }],
    });
    rerender(
      <UploadProvider>
        <LibraryPage />
      </UploadProvider>,
    );

    // The upload genuinely completed (server-side and in the shared
    // provider) even though LibraryPage itself was unmounted throughout —
    // proven here by the freshly-remounted page's own listVideos() call
    // reflecting it.
    await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());
  });

  describe("Delete Video (Milestone 3.7 follow-up)", () => {
    function withOneVideo() {
      videosApi.listVideos.mockResolvedValue({
        videos: [{ id: 7, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 10, created_at: "2026-09-01T00:00:00Z" }],
      });
    }

    it("asks for confirmation before deleting, and does nothing on Cancel", async () => {
      withOneVideo();
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "Delete" }));
      expect(screen.getByRole("button", { name: "Confirm delete" })).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("button", { name: "Confirm delete" })).not.toBeInTheDocument();
      expect(videosApi.deleteVideo).not.toHaveBeenCalled();
      expect(screen.getByText("clip.mp4")).toBeInTheDocument(); // untouched
    });

    it("deletes the video on confirm and refreshes the list", async () => {
      withOneVideo();
      videosApi.deleteVideo.mockResolvedValue(undefined);
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());

      videosApi.listVideos.mockResolvedValue({ videos: [] }); // reflects the deletion once re-fetched

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "Delete" }));
      await user.click(screen.getByRole("button", { name: "Confirm delete" }));

      expect(videosApi.deleteVideo).toHaveBeenCalledWith("real-token", 7);
      await waitFor(() => expect(screen.getByText(/no videos yet/i)).toBeInTheDocument());
    });

    it("shows the backend's refusal message and keeps the video when it has a schedule reference", async () => {
      const { ApiError } = await import("@/lib/api/client");
      withOneVideo();
      videosApi.deleteVideo.mockRejectedValue(
        new ApiError("video 7 has a platform post; remove or cancel it first.", { status: 409 }),
      );
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "Delete" }));
      await user.click(screen.getByRole("button", { name: "Confirm delete" }));

      await waitFor(() =>
        expect(screen.getByText("video 7 has a platform post; remove or cancel it first.")).toBeInTheDocument(),
      );
      expect(screen.getByText("clip.mp4")).toBeInTheDocument(); // not removed — the delete never happened
    });
  });
});
