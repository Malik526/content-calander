import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LibraryPage from "@/app/app/library/page";

/**
 * Library's real backend wiring (Milestone 3.7) — lib/api/videos.ts is
 * mocked directly (its own HTTP behavior is covered by
 * tests/lib/videos.test.ts), so these tests focus on the page's own
 * loading/empty/populated/error states and the upload flow, mirroring
 * tests/routes/settings-tiktok.test.tsx's established pattern exactly.
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
}));
vi.mock("@/lib/api/videos", () => videosApi);

afterEach(() => {
  vi.clearAllMocks();
});

function makeFile(name: string, content = "video bytes"): File {
  return new File([content], name, { type: "video/mp4" });
}

describe("LibraryPage", () => {
  it("shows the empty state when the account has no videos", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<LibraryPage />);

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

    render(<LibraryPage />);

    await waitFor(() => expect(screen.getByText("clip-a.mp4")).toBeInTheDocument());
    expect(screen.getByText("clip-b.mp4")).toBeInTheDocument();
  });

  it("shows an error state with retry when loading videos fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    videosApi.listVideos.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    videosApi.listVideos.mockResolvedValueOnce({ videos: [] });

    render(<LibraryPage />);

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

    render(<LibraryPage />);
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

  it("shows a request-level error without touching the video list on total failure", async () => {
    const { ApiError } = await import("@/lib/api/client");
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    videosApi.uploadVideos.mockRejectedValue(new ApiError("Could not upload your videos."));

    render(<LibraryPage />);
    await waitFor(() => expect(videosApi.listVideos).toHaveBeenCalledTimes(1));

    const user = userEvent.setup();
    const input = screen.getByLabelText(/upload videos/i);
    await user.upload(input, [makeFile("clip.mp4")]);
    await user.click(screen.getByRole("button", { name: /upload 1 video/i }));

    await waitFor(() => expect(screen.getByText("Could not upload your videos.")).toBeInTheDocument());
    expect(videosApi.listVideos).toHaveBeenCalledTimes(1); // never called again — request itself failed
  });
});
