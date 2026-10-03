import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUploadManager } from "@/lib/uploads";
import { renderWithProviders } from "@/tests/test-utils";

/**
 * lib/uploads.tsx in isolation (Milestone 3.7 follow-up — navigation/
 * upload lifecycle review). tests/routes/library.test.tsx covers the
 * page-level "survives navigation" behavior; these tests cover the
 * provider/hook contract itself directly, mirroring
 * tests/lib/session.test.tsx's Probe-component pattern.
 */

const videosApi = vi.hoisted(() => ({ uploadVideos: vi.fn() }));
vi.mock("@/lib/api/videos", () => videosApi);

// Milestone 3.15: UploadProvider refreshes the user's cached videos after a
// batch, so it reads the session's user id.
const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "creator@example.com", displayName: "Creator" },
  accessToken: "real-token",
  status: "authenticated" as const,
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

function Probe() {
  const { uploading, lastResults, error, upload } = useUploadManager();
  return (
    <div>
      <span data-testid="uploading">{String(uploading)}</span>
      <span data-testid="results">{lastResults ? JSON.stringify(lastResults) : ""}</span>
      <span data-testid="error">{error ?? ""}</span>
      <button onClick={() => void upload([new File(["x"], "x.mp4")], "token")}>upload</button>
    </div>
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useUploadManager", () => {
  it("throws when used outside an UploadProvider", () => {
    // Suppress the expected React error-boundary console noise for this
    // one intentionally-broken render.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/useUploadManager must be used within an UploadProvider/);
    spy.mockRestore();
  });

  it("sets uploading true while the request is in flight, then false with the results", async () => {
    let resolveUpload: (value: { results: unknown[] }) => void;
    videosApi.uploadVideos.mockReturnValue(new Promise((resolve) => (resolveUpload = resolve)));

    renderWithProviders(<Probe />);
    expect(screen.getByTestId("uploading")).toHaveTextContent("false");

    screen.getByRole("button", { name: "upload" }).click();
    await waitFor(() => expect(screen.getByTestId("uploading")).toHaveTextContent("true"));

    resolveUpload!({ results: [{ filename: "x.mp4", success: true, video: null, error: null }] });

    await waitFor(() => expect(screen.getByTestId("uploading")).toHaveTextContent("false"));
    expect(screen.getByTestId("results")).toHaveTextContent("x.mp4");
  });

  it("sets a request-level error and leaves uploading false on total failure", async () => {
    const { ApiError } = await import("@/lib/api/client");
    videosApi.uploadVideos.mockRejectedValue(new ApiError("Could not upload your videos."));

    renderWithProviders(<Probe />);

    screen.getByRole("button", { name: "upload" }).click();

    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("Could not upload your videos."));
    expect(screen.getByTestId("uploading")).toHaveTextContent("false");
  });

  it("state set by one consumer is visible to a second, sibling consumer under the same provider", async () => {
    videosApi.uploadVideos.mockResolvedValue({ results: [{ filename: "x.mp4", success: true, video: null, error: null }] });

    renderWithProviders(
      <>
        <Probe />
        <Probe />
      </>,
    );

    const buttons = screen.getAllByRole("button", { name: "upload" });
    buttons[0].click();

    const resultsSpans = await waitFor(() => {
      const spans = screen.getAllByTestId("results");
      expect(spans[0]).toHaveTextContent("x.mp4");
      return spans;
    });
    // both Probe instances read from the same shared provider state
    expect(resultsSpans[1]).toHaveTextContent("x.mp4");
  });
});
