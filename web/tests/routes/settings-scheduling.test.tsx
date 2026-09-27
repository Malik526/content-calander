import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/app/settings/page";

/**
 * Settings' hosted posting-cadence section (Milestone 3.8) —
 * lib/api/cadence.ts is mocked directly, same pattern as
 * tests/routes/settings-tiktok.test.tsx mocking lib/api/platforms.
 * lib/api/platforms is also mocked here so the TikTok section (rendered
 * on the same page) doesn't produce an unrelated error alert.
 */

const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "creator@example.com", displayName: "Creator" },
  accessToken: "real-token",
  status: "authenticated" as const,
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

const platformsApi = vi.hoisted(() => ({
  getTikTokConnection: vi.fn(),
  connectTikTok: vi.fn(),
  disconnectTikTok: vi.fn(),
}));
vi.mock("@/lib/api/platforms", () => platformsApi);

const cadenceApi = vi.hoisted(() => ({
  getCadence: vi.fn(),
  saveCadence: vi.fn(),
  getUpcomingSlots: vi.fn(),
}));
vi.mock("@/lib/api/cadence", () => cadenceApi);

beforeEach(() => {
  platformsApi.getTikTokConnection.mockResolvedValue({
    platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SettingsPage — Scheduling", () => {
  it("shows an empty state when no cadence is configured yet", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("No posting times set yet.")).toBeInTheDocument());
    expect(cadenceApi.getUpcomingSlots).not.toHaveBeenCalled();
  });

  it("loads an existing cadence and its upcoming slots", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({
      slots: [{ id: 1, scheduled_at: "2026-10-05T09:00:00", status: "OPEN", timezone: "America/New_York" }],
    });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("monday 09:00")).toBeInTheDocument());
    expect(await screen.findByText("2026-10-05T09:00:00")).toBeInTheDocument();
    expect(cadenceApi.getUpcomingSlots).toHaveBeenCalledWith("real-token");
  });

  it("adds a posting time and saves the cadence with the session's access token", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    cadenceApi.saveCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("No posting times set yet.")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add time" }));
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    await waitFor(() =>
      expect(cadenceApi.saveCadence).toHaveBeenCalledWith(
        "real-token",
        expect.objectContaining({
          posting_times: [{ weekday: "monday", posting_time: "09:00" }],
        }),
      ),
    );
    expect(await screen.findByText("monday 09:00")).toBeInTheDocument();
  });

  it("removes a posting time from the pending edit before saving", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "friday", posting_time: "18:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("friday 18:00")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(screen.getByText("No posting times set yet.")).toBeInTheDocument();
  });

  it("shows an error state with retry when loading the cadence fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    cadenceApi.getCadence.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    cadenceApi.getCadence.mockResolvedValueOnce({ configured: false, timezone: null, is_active: false, posting_times: [] });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("Could not reach the server.")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("No posting times set yet.")).toBeInTheDocument());
  });
});
