import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AppHomePage from "@/app/app/page";
import { SessionProvider } from "@/lib/session";

/**
 * /app activation/status home (Milestone 3.14 UX cleanup) — lib/api/platforms,
 * cadence, and videos are mocked directly. The page fetches these three in
 * parallel when it has an accessToken; without one (dev-mock session in the
 * test environment) it shows the quick-link fallback instead.
 */

const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "creator@example.com", displayName: "Creator" },
  accessToken: "real-token",
  status: "authenticated" as const,
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/session")>();
  return { ...real, useSession: () => mockSession };
});

const platformsApi = vi.hoisted(() => ({
  getTikTokConnection: vi.fn(),
  connectTikTok: vi.fn(),
  disconnectTikTok: vi.fn(),
  getMe: vi.fn(),
}));
vi.mock("@/lib/api/platforms", () => platformsApi);

const cadenceApi = vi.hoisted(() => ({
  getCadence: vi.fn(),
  saveCadence: vi.fn(),
  getUpcomingSlots: vi.fn(),
}));
vi.mock("@/lib/api/cadence", () => cadenceApi);

const videosApi = vi.hoisted(() => ({
  listVideos: vi.fn(),
  uploadVideos: vi.fn(),
  deleteVideo: vi.fn(),
}));
vi.mock("@/lib/api/videos", () => videosApi);

afterEach(() => vi.clearAllMocks());

function renderHome() {
  return render(
    <SessionProvider>
      <AppHomePage />
    </SessionProvider>,
  );
}

function mockUnconfigured() {
  platformsApi.getTikTokConnection.mockResolvedValue({ platform: "tiktok", connected: false, status: "disconnected", account_label: null });
  cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
  videosApi.listVideos.mockResolvedValue({ videos: [] });
}

function mockFullyConfigured() {
  platformsApi.getTikTokConnection.mockResolvedValue({ platform: "tiktok", connected: true, status: "connected", account_label: "My Account", creator_username: "picklebatch" });
  cadenceApi.getCadence.mockResolvedValue({ configured: true, timezone: "America/New_York", is_active: true, posting_times: [{ weekday: "monday", posting_time: "18:00" }] });
  videosApi.listVideos.mockResolvedValue({
    videos: [
      { id: 1, original_filename: "a.mp4", status: "ASSIGNED", file_size_bytes: 1000, created_at: "2026-09-01T00:00:00Z", assigned_slot_id: 3 },
      { id: 2, original_filename: "b.mp4", status: "DISCOVERED", file_size_bytes: 2000, created_at: "2026-09-02T00:00:00Z", assigned_slot_id: null },
    ],
  });
}

describe("AppHomePage — activation checklist", () => {
  it("shows setup prompt heading for an unconfigured account", async () => {
    mockUnconfigured();
    renderHome();
    await waitFor(() => expect(screen.getByText("Get Pickle Batch ready.")).toBeInTheDocument());
  });

  it("shows all four setup steps for a fully unconfigured account", async () => {
    mockUnconfigured();
    renderHome();
    await waitFor(() => expect(screen.getByText("Connect TikTok")).toBeInTheDocument());
    expect(screen.getByText("Set up posting schedule")).toBeInTheDocument();
    expect(screen.getByText("Upload videos")).toBeInTheDocument();
    expect(screen.getByText("Add videos to your Queue")).toBeInTheDocument();
  });

  it("links to the correct section for each unconfigured step", async () => {
    mockUnconfigured();
    renderHome();
    await waitFor(() => expect(screen.getByText("Connect TikTok")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /connect in settings/i })).toHaveAttribute("href", "/app/settings");
    expect(screen.getAllByRole("link", { name: /set up in queue/i })[0]).toHaveAttribute("href", "/app/queue");
    expect(screen.getByRole("link", { name: /upload in library/i })).toHaveAttribute("href", "/app/library");
  });

  it("shows pipeline active heading when all steps are done", async () => {
    mockFullyConfigured();
    renderHome();
    await waitFor(() => expect(screen.getByText("Your posting pipeline is active.")).toBeInTheDocument());
  });

  it("shows done state details when all steps are configured", async () => {
    mockFullyConfigured();
    renderHome();
    await waitFor(() => expect(screen.getByText("@picklebatch")).toBeInTheDocument());
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    expect(screen.getByText("2 videos")).toBeInTheDocument();
    expect(screen.getByText("1 scheduled")).toBeInTheDocument();
  });

  it("shows TikTok as done and others as not-done in a partial state", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({ platform: "tiktok", connected: true, status: "connected", account_label: null });
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderHome();
    await waitFor(() => expect(screen.getByText("Connect TikTok")).toBeInTheDocument());

    // TikTok done — shows detail text, not a CTA
    expect(screen.getByText("Connected")).toBeInTheDocument();
    // Next step — shows CTA
    expect(screen.getByRole("link", { name: /set up in queue/i })).toBeInTheDocument();
  });

  it("saves timezone as the used value", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/Los_Angeles", is_active: true, posting_times: [],
    });
    platformsApi.getTikTokConnection.mockResolvedValue({ platform: "tiktok", connected: false, status: "disconnected", account_label: null });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderHome();
    // Should render Los Angeles as the cadence timezone detail once schedule is done
    await waitFor(() => expect(screen.getByText("America/Los_Angeles")).toBeInTheDocument());
  });
});
