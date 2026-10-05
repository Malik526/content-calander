import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/app/settings/page";
import { renderWithProviders } from "@/tests/test-utils";

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
  getInstagramConnection: vi.fn(),
}));
vi.mock("@/lib/api/platforms", () => platformsApi);

const INSTAGRAM_NOT_CONNECTED = {
  platform: "instagram", connected: false, status: "DISCONNECTED", account_label: null, connect_available: false,
};

beforeEach(() => {
  platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_NOT_CONNECTED);
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/app/settings");
});

describe("SettingsPage — TikTok connection", () => {
  it("shows disconnected state after loading", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(within(screen.getByRole("group", { name: "tiktok connection" })).getByText("Not connected")).toBeInTheDocument());
    expect(platformsApi.getTikTokConnection).toHaveBeenCalledWith("real-token");
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("shows connected state with the account label", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: "my_tiktok_handle",
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    expect(screen.getByText("my_tiktok_handle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  // Milestone 3.14 follow-up — which TikTok account is connected.
  it("shows Connected as @username", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: "@pickle.creator",
      creator_username: "pickle.creator", creator_nickname: "Pickle Creator", creator_avatar_url: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByTestId("tiktok-connected-as")).toHaveTextContent("Connected as @pickle.creator"));
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("shows the nickname when TikTok gave no username", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: "Pickle Creator",
      creator_username: null, creator_nickname: "Pickle Creator", creator_avatar_url: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByTestId("tiktok-connected-as")).toHaveTextContent("Connected as Pickle Creator"));
  });

  it("shows just Connected when the account identity is unknown", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: true, status: "ACTIVE", account_label: null,
      creator_username: null, creator_nickname: null, creator_avatar_url: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    expect(screen.queryByTestId("tiktok-connected-as")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("shows an error state with retry when loading the status fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.getTikTokConnection.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    platformsApi.getTikTokConnection.mockResolvedValueOnce({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server."));

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(within(screen.getByRole("group", { name: "tiktok connection" })).getByText("Not connected")).toBeInTheDocument());
  });

  it("calls connectTikTok with the session's access token when Connect is clicked", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });
    platformsApi.connectTikTok.mockReturnValue(new Promise(() => {})); // never resolves — just prove it was called

    renderWithProviders(<SettingsPage />);
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

    renderWithProviders(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(within(screen.getByRole("group", { name: "tiktok connection" })).getByText("Not connected")).toBeInTheDocument());
    expect(platformsApi.disconnectTikTok).toHaveBeenCalledWith("real-token");
  });

  it("shows a clear error message for a denied TikTok callback, and strips the query param", async () => {
    window.history.replaceState({}, "", "/app/settings?tiktok=denied");
    platformsApi.getTikTokConnection.mockResolvedValue({
      platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null,
    });

    renderWithProviders(<SettingsPage />);

    await waitFor(() => expect(screen.getByText(/denied or cancelled/i)).toBeInTheDocument());
    expect(window.location.search).toBe("");
  });
});

// Milestone 4.0: Instagram is shown with its real status, read-only.
describe("SettingsPage — Instagram card", () => {
  const TIKTOK_CONNECTED = { platform: "tiktok", connected: true, status: "ACTIVE", account_label: null };

  it("shows Instagram as not connected, says connecting is coming, and offers no Instagram button", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue(TIKTOK_CONNECTED);
    renderWithProviders(<SettingsPage />);

    const instagramCard = within(await screen.findByRole("group", { name: "instagram connection" }));
    expect(instagramCard.getByText("Not connected")).toBeInTheDocument();
    expect(instagramCard.getByText("Connecting Instagram is coming soon.")).toBeInTheDocument();
    expect(instagramCard.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /connect/i }).map((b) => b.textContent)).toEqual(["Disconnect"]);
  });

  it("keeps TikTok's card and actions unchanged next to Instagram", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue({ ...TIKTOK_CONNECTED, connected: false, status: "DISCONNECTED" });
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(await screen.findByText("Connecting Instagram is coming soon.")).toBeInTheDocument();
    expect(screen.getAllByText("Not connected")).toHaveLength(2);
  });

  it("shows a connected Instagram account without the coming-soon note", async () => {
    platformsApi.getTikTokConnection.mockResolvedValue(TIKTOK_CONNECTED);
    platformsApi.getInstagramConnection.mockResolvedValue({
      ...INSTAGRAM_NOT_CONNECTED, connected: true, status: "ACTIVE", account_label: "@picklebatch",
    });
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("@picklebatch")).toBeInTheDocument();
    expect(screen.queryByText("Connecting Instagram is coming soon.")).not.toBeInTheDocument();
    expect(screen.getAllByText("Connected")).toHaveLength(2);
  });

  it("an Instagram load failure is shown with Retry and doesn't affect TikTok", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.getTikTokConnection.mockResolvedValue(TIKTOK_CONNECTED);
    platformsApi.getInstagramConnection.mockRejectedValue(new ApiError("down", { status: 503 }));
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("Could not load your Instagram connection.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });
});
