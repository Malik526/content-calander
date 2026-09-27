import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/app/settings/page";

/**
 * Settings' real TikTok connection wiring (Milestone 3.6) — lib/api/platforms.ts
 * is mocked directly (its own HTTP behavior is covered by
 * tests/lib/platforms.test.ts) so these tests focus on the page's own
 * loading/connected/disconnected/error state transitions and that the
 * real session's accessToken is what gets threaded through.
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

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/app/settings");
});

describe("SettingsPage — TikTok connection", () => {
  it("shows disconnected state after loading", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());
    expect(platformsApi.getTikTokConnection).toHaveBeenCalledWith("real-token");
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("shows connected state with the account label", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: "my_tiktok_handle",
    });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    expect(screen.getByText("my_tiktok_handle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("shows an error state with retry when loading the status fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.getTikTokConnection.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    platformsApi.getTikTokConnection.mockResolvedValueOnce({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server."));

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());
  });

  it("calls connectTikTok with the session's access token when Connect is clicked", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });
    platformsApi.connectTikTok.mockReturnValue(new Promise(() => {})); // never resolves — just prove it was called

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(platformsApi.connectTikTok).toHaveBeenCalledWith("real-token");
  });

  it("calls disconnectTikTok and updates to disconnected state", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: "handle",
    });
    platformsApi.disconnectTikTok.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: "handle",
    });

    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(screen.getByText("Not connected")).toBeInTheDocument());
    expect(platformsApi.disconnectTikTok).toHaveBeenCalledWith("real-token");
  });

  it("shows a clear error message for a denied TikTok callback, and strips the query param", async () => {
    window.history.replaceState({}, "", "/app/settings?tiktok=denied");
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    render(<SettingsPage />);

    await waitFor(() => expect(screen.getByText(/denied or cancelled/i)).toBeInTheDocument());
    expect(window.location.search).toBe("");
  });
});
