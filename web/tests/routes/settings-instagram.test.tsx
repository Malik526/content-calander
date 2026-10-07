import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/app/settings/page";
import { PlatformId } from "@/lib/domain/publishing";
import { queryKeys } from "@/lib/query/keys";
import { renderWithProviders } from "@/tests/test-utils";

/**
 * Settings' Instagram connection (Milestone 4.1): Connect / Disconnect,
 * "@username", callback outcomes, the user-scoped Instagram cache entry, and
 * independence from TikTok's actions. lib/api/platforms.ts is mocked; its
 * HTTP behavior is covered by tests/lib/platforms.test.ts.
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
  connectInstagram: vi.fn(),
  disconnectInstagram: vi.fn(),
}));
vi.mock("@/lib/api/platforms", () => platformsApi);

const INSTAGRAM_AVAILABLE = {
  platform: "instagram", connected: false, status: "DISCONNECTED", account_label: null, connect_available: true,
};
const INSTAGRAM_CONNECTED = { ...INSTAGRAM_AVAILABLE, connected: true, status: "ACTIVE", account_label: "@pickle.batch" };
const TIKTOK_DISCONNECTED = { platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null };
const INSTAGRAM_KEY = queryKeys.platformConnection("u1", PlatformId.INSTAGRAM);

const instagramCard = async () => within(await screen.findByRole("group", { name: "instagram connection" }));
const tiktokCard = async () => within(await screen.findByRole("group", { name: "tiktok connection" }));

beforeEach(() => {
  platformsApi.getTikTokConnection.mockResolvedValue(TIKTOK_DISCONNECTED);
  platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_AVAILABLE);
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/app/settings");
});

describe("SettingsPage — Instagram connection", () => {
  it("offers Connect when the server reports connect_available", async () => {
    renderWithProviders(<SettingsPage />);
    const card = await instagramCard();
    expect(await card.findByRole("button", { name: "Connect" })).toBeEnabled();
    expect(card.getByText("Not connected")).toBeInTheDocument();
    expect(screen.queryByText("Connecting Instagram is coming soon.")).not.toBeInTheDocument();
  });

  it("offers no Connect button when the server isn't configured for Instagram", async () => {
    platformsApi.getInstagramConnection.mockResolvedValue({ ...INSTAGRAM_AVAILABLE, connect_available: false });
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("Connecting Instagram is coming soon.")).toBeInTheDocument();
    expect((await instagramCard()).queryByRole("button")).not.toBeInTheDocument();
  });

  it("starts Instagram authorization with the session's token and shows progress", async () => {
    platformsApi.connectInstagram.mockReturnValue(new Promise(() => {})); // navigation never happens in tests
    renderWithProviders(<SettingsPage />);
    const card = await instagramCard();

    await userEvent.setup().click(await card.findByRole("button", { name: "Connect" }));

    expect(platformsApi.connectInstagram).toHaveBeenCalledWith("real-token");
    expect(card.getByRole("button", { name: "Connecting…" })).toBeDisabled();
    expect(platformsApi.connectTikTok).not.toHaveBeenCalled();
    // TikTok's own Connect is unaffected by Instagram's progress.
    expect((await tiktokCard()).getByRole("button", { name: "Connect" })).toBeEnabled();
  });

  it("shows a failed Instagram connect start without touching TikTok", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.connectInstagram.mockRejectedValue(new ApiError("Connecting Instagram is not available on this server.", { status: 503 }));
    renderWithProviders(<SettingsPage />);
    const card = await instagramCard();

    await userEvent.setup().click(await card.findByRole("button", { name: "Connect" }));

    expect(await screen.findByText("Connecting Instagram is not available on this server.")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Connect" })).toBeEnabled();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect((await tiktokCard()).getByRole("button", { name: "Connect" })).toBeEnabled();
  });

  it("shows the connected account as @username", async () => {
    platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_CONNECTED);
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByTestId("instagram-connected-as")).toHaveTextContent("Connected as @pickle.batch");
    expect((await instagramCard()).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("shows plain Connected when Instagram didn't report a username", async () => {
    platformsApi.getInstagramConnection.mockResolvedValue({ ...INSTAGRAM_CONNECTED, account_label: null });
    renderWithProviders(<SettingsPage />);
    const card = await instagramCard();
    expect(await card.findByText("Connected")).toBeInTheDocument();
    expect(screen.queryByTestId("instagram-connected-as")).not.toBeInTheDocument();
  });

  it("disconnects, writes the result into the Instagram cache entry, and leaves TikTok alone", async () => {
    platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_CONNECTED);
    platformsApi.disconnectInstagram.mockResolvedValue(INSTAGRAM_AVAILABLE);
    const { queryClient } = renderWithProviders(<SettingsPage />);
    const card = await instagramCard();

    await userEvent.setup().click(await card.findByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(card.getByText("Not connected")).toBeInTheDocument());
    expect(platformsApi.disconnectInstagram).toHaveBeenCalledWith("real-token");
    expect(queryClient.getQueryData(INSTAGRAM_KEY)).toEqual(INSTAGRAM_AVAILABLE);
    expect(card.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(platformsApi.disconnectTikTok).not.toHaveBeenCalled();
  });

  it("shows a failed disconnect and keeps the account connected", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_CONNECTED);
    platformsApi.disconnectInstagram.mockRejectedValue(new ApiError("Could not reach the server."));
    renderWithProviders(<SettingsPage />);
    const card = await instagramCard();

    await userEvent.setup().click(await card.findByRole("button", { name: "Disconnect" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
    expect(card.getByRole("button", { name: "Disconnect" })).toBeEnabled();
    expect(card.getByText("Connected")).toBeInTheDocument();
  });
});

describe("SettingsPage — Instagram callback outcome", () => {
  it("confirms a successful connection, refetches the status, and removes only the instagram parameter", async () => {
    window.history.replaceState({}, "", "/app/settings?tab=platforms&instagram=connected");
    platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_CONNECTED);

    renderWithProviders(<SettingsPage />);

    expect(await screen.findByRole("status")).toHaveTextContent("Instagram connected.");
    expect(await screen.findByTestId("instagram-connected-as")).toHaveTextContent("@pickle.batch");
    expect(window.location.search).toBe("?tab=platforms");
    await waitFor(() => expect(platformsApi.getInstagramConnection.mock.calls.length).toBeGreaterThanOrEqual(1));
  });

  it("invalidates a previously cached Instagram status when the callback lands", async () => {
    window.history.replaceState({}, "", "/app/settings?instagram=connected");
    platformsApi.getInstagramConnection.mockResolvedValue(INSTAGRAM_CONNECTED);
    const { createTestQueryClient } = await import("@/tests/test-utils");
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(INSTAGRAM_KEY, INSTAGRAM_AVAILABLE); // stale "not connected" from before the redirect

    renderWithProviders(<SettingsPage />, { queryClient });

    expect(await screen.findByTestId("instagram-connected-as")).toHaveTextContent("@pickle.batch");
    expect(platformsApi.getInstagramConnection).toHaveBeenCalled();
  });

  it.each([
    ["denied", /denied or cancelled/i],
    ["invalid_state", /could not be verified/i],
    ["expired_state", /attempt expired/i],
    ["exchange_failed", /could not be reached/i],
    ["unavailable", /isn't available right now/i],
    ["something_new", /something went wrong connecting instagram/i],
  ])("explains the %s outcome", async (outcome, message) => {
    window.history.replaceState({}, "", `/app/settings?instagram=${outcome}`);
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(window.location.search).toBe("");
  });

  it("keeps TikTok's callback handling separate from Instagram's", async () => {
    window.history.replaceState({}, "", "/app/settings?tiktok=denied");
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("TikTok authorization was denied or cancelled.")).toBeInTheDocument();
    expect(screen.queryByText(/Instagram authorization/i)).not.toBeInTheDocument();
    expect(await (await instagramCard()).findByRole("button", { name: "Connect" })).toBeEnabled();
  });
});
