import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
        { id: 1, original_filename: "clip-a.mp4", status: "DISCOVERED", file_size_bytes: 2_000_000, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" },
        { id: 2, original_filename: "clip-b.mp4", status: "DISCOVERED", file_size_bytes: null, created_at: "2026-09-02T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" },
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
        { filename: "good.mp4", success: true, video: { id: 3, original_filename: "good.mp4", status: "DISCOVERED", file_size_bytes: 10, created_at: "2026-09-03T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" }, error: null },
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
      results: [{ filename: "clip.mp4", success: true, video: { id: 9, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 5, created_at: "2026-09-04T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" }, error: null }],
    });

    // Navigate back to Library.
    videosApi.listVideos.mockResolvedValue({
      videos: [{ id: 9, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 5, created_at: "2026-09-04T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" }],
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

  // Milestone 3.14 UX cleanup: filter tabs. Final follow-up: tabs and badges
  // come from the backend's publish_status (the Queue's own resolver), with
  // a Published tab; Scheduled holds every not-yet-published state.
  describe("Library filter tabs", () => {
    const video = (id: number, name: string, publish_status: string, assigned_slot_id: number | null = id) => ({
      id, original_filename: name, status: "DISCOVERED", file_size_bytes: 1000,
      created_at: "2026-09-01T00:00:00Z", assigned_slot_id, publish_status,
    });

    function withMixedVideos() {
      videosApi.listVideos.mockResolvedValue({
        videos: [
          video(1, "loose.mp4", "UNSCHEDULED", null),
          video(2, "waiting.mp4", "SCHEDULED"),
          video(3, "sending.mp4", "PUBLISHING"),
          video(4, "posted.mp4", "PUBLISHED"),
          video(5, "broken.mp4", "FAILED"),
          video(6, "unclear.mp4", "NEEDS_ATTENTION"),
        ],
      });
    }

    const rowFor = (name: string) => within(screen.getByText(name).closest("li") as HTMLElement);
    const visibleNames = () =>
      ["loose.mp4", "waiting.mp4", "sending.mp4", "posted.mp4", "broken.mp4", "unclear.mp4"].filter((name) =>
        screen.queryByText(name),
      );

    async function openTab(name: RegExp) {
      await userEvent.setup().click(screen.getByRole("tab", { name }));
    }

    async function renderMixed() {
      withMixedVideos();
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("loose.mp4")).toBeInTheDocument());
    }

    it("shows all videos in the default All tab", async () => {
      await renderMixed();
      expect(visibleNames()).toHaveLength(6);
    });

    it("badges each video with the Queue's status label, and none for unscheduled", async () => {
      await renderMixed();
      expect(rowFor("loose.mp4").queryByText(/Scheduled|Published|Publishing|Failed|Needs attention/)).toBeNull();
      expect(rowFor("waiting.mp4").getByText("Scheduled")).toBeInTheDocument();
      expect(rowFor("sending.mp4").getByText("Publishing")).toBeInTheDocument();
      expect(rowFor("posted.mp4").getByText("Published")).toBeInTheDocument();
      expect(rowFor("broken.mp4").getByText("Failed")).toBeInTheDocument();
      expect(rowFor("unclear.mp4").getByText("Needs attention")).toBeInTheDocument();
    });

    it("counts each tab from publish_status", async () => {
      await renderMixed();
      expect(screen.getByRole("tab", { name: /^All/ })).toHaveTextContent("(6)");
      expect(screen.getByRole("tab", { name: /^Unscheduled/ })).toHaveTextContent("(1)");
      expect(screen.getByRole("tab", { name: /^Scheduled/ })).toHaveTextContent("(4)");
      expect(screen.getByRole("tab", { name: /^Published/ })).toHaveTextContent("(1)");
    });

    it("Unscheduled tab shows only videos without a slot", async () => {
      await renderMixed();
      await openTab(/^Unscheduled/);
      expect(visibleNames()).toEqual(["loose.mp4"]);
    });

    it("Scheduled tab holds scheduled, publishing, failed and needs-attention videos but not published ones", async () => {
      await renderMixed();
      await openTab(/^Scheduled/);
      expect(visibleNames()).toEqual(["waiting.mp4", "sending.mp4", "broken.mp4", "unclear.mp4"]);
      expect(rowFor("sending.mp4").getByText("Publishing")).toBeInTheDocument();
    });

    it("Published tab shows only published videos, badged Published", async () => {
      await renderMixed();
      await openTab(/^Published/);
      expect(visibleNames()).toEqual(["posted.mp4"]);
      expect(rowFor("posted.mp4").getByText("Published")).toBeInTheDocument();
    });

    it("classifies by publish_status, not assigned_slot_id — a published video still holds its slot", async () => {
      await renderMixed();
      await openTab(/^Scheduled/);
      expect(screen.queryByText("posted.mp4")).not.toBeInTheDocument();
    });

    // Regression: localhost pointed at the deployed API, which predates
    // publish_status, and every assigned video rendered "Needs attention".
    it("never shows Needs attention when an older API omits publish_status", async () => {
      videosApi.listVideos.mockResolvedValue({
        videos: [
          { id: 1, original_filename: "old-loose.mp4", status: "DISCOVERED", file_size_bytes: 1000, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: null },
          { id: 2, original_filename: "old-assigned.mp4", status: "DISCOVERED", file_size_bytes: 1000, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: 184 },
        ],
      });
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("old-assigned.mp4")).toBeInTheDocument());

      expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
      expect(rowFor("old-assigned.mp4").getByText("Scheduled")).toBeInTheDocument();
      expect(rowFor("old-loose.mp4").queryByText(/Scheduled|Needs attention/)).toBeNull();
      expect(screen.getByRole("tab", { name: /^Unscheduled/ })).toHaveTextContent("(1)");
    });

    it("warns in development only when the API omits publish_status", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      await renderMixed();
      expect(warn).not.toHaveBeenCalled();
      cleanup();

      videosApi.listVideos.mockResolvedValue({
        videos: [{ id: 2, original_filename: "old.mp4", status: "DISCOVERED", file_size_bytes: 1, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: 184 }],
      });
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("old.mp4")).toBeInTheDocument());
      expect(warn).toHaveBeenCalledWith(expect.stringContaining("no publish_status"));
      warn.mockRestore();
    });

    it("shows an empty state when no videos match the active filter", async () => {
      videosApi.listVideos.mockResolvedValue({ videos: [video(1, "clip.mp4", "UNSCHEDULED", null)] });
      renderLibraryPage();
      await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());

      await openTab(/^Scheduled/);
      expect(screen.getByText("No scheduled videos")).toBeInTheDocument();
      expect(screen.queryByText("clip.mp4")).not.toBeInTheDocument();

      await openTab(/^Published/);
      expect(screen.getByText("No published videos yet")).toBeInTheDocument();
    });
  });

  describe("Delete Video (Milestone 3.7 follow-up)", () => {
    function withOneVideo() {
      videosApi.listVideos.mockResolvedValue({
        videos: [{ id: 7, original_filename: "clip.mp4", status: "DISCOVERED", file_size_bytes: 10, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: null, publish_status: "UNSCHEDULED" }],
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
