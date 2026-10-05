import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AppHomePage from "@/app/app/page";
import LibraryPage from "@/app/app/library/page";
import QueuePage from "@/app/app/queue/page";
import SettingsPage from "@/app/app/settings/page";
import { queryKeys } from "@/lib/query/keys";
import { renderWithProviders } from "@/tests/test-utils";

/**
 * Milestone 3.15 acceptance tests for the client state architecture
 * (docs/decisions/0017-client-server-state-cache.md). Navigation is
 * simulated the way the real App Router does it: the layout's providers
 * stay mounted (one renderWithProviders tree) while only the page under
 * them is swapped. API modules are mocked; what's asserted is what the
 * user sees and how many requests are made, not TanStack internals.
 */

const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "a@example.com", displayName: "User A" } as { id: string; email: string; displayName: string } | null,
  accessToken: "token-a" as string | null,
  status: "authenticated" as "authenticated" | "unauthenticated",
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

const videosApi = vi.hoisted(() => ({ listVideos: vi.fn(), uploadVideos: vi.fn(), deleteVideo: vi.fn() }));
vi.mock("@/lib/api/videos", () => videosApi);
const cadenceApi = vi.hoisted(() => ({ getCadence: vi.fn(), saveCadence: vi.fn(), getUpcomingSlots: vi.fn() }));
vi.mock("@/lib/api/cadence", () => cadenceApi);
const queueApi = vi.hoisted(() => ({
  listQueueSlots: vi.fn(), assignVideoToSlot: vi.fn(), assignNextOpenSlot: vi.fn(), unassignSlot: vi.fn(),
  retrySlotPublication: vi.fn(),
}));
vi.mock("@/lib/api/queue", () => queueApi);
const platformsApi = vi.hoisted(() => ({
  getMe: vi.fn(), getTikTokConnection: vi.fn(), connectTikTok: vi.fn(), disconnectTikTok: vi.fn(),
  getInstagramConnection: vi.fn(),
}));
vi.mock("@/lib/api/platforms", () => platformsApi);

const video = (id: number, name: string, publish_status = "UNSCHEDULED", assigned_slot_id: number | null = null) => ({
  id, original_filename: name, status: "DISCOVERED", file_size_bytes: 1000, created_at: "2026-10-01T00:00:00Z",
  assigned_slot_id, publish_status,
});

const openSlot = {
  id: 50, scheduled_at: "2099-01-05T09:00:00", timezone: "America/New_York", status: "OPEN", display_status: "OPEN",
  reason_code: null, message: null, action_hint: null, published_at: null, can_unassign: false, can_retry: false,
  retry_requires_confirmation: false, assigned_video: null, platform_post_status: null, publications: [],
};

/** The Queue's "Unscheduled videos" section (the slot picker also lists names). */
const unscheduledSection = () => screen.findByRole("region", { name: "Unscheduled videos" });

const CONNECTED = { platform: "tiktok", connected: true, status: "connected", account_label: null, creator_username: "picklebatch" };
const DISCONNECTED = { platform: "tiktok", connected: false, status: "not_connected", account_label: null };
const CADENCE = { configured: true, timezone: "America/New_York", is_active: true, posting_times: [{ weekday: "monday", posting_time: "09:00" }] };

beforeEach(() => {
  mockSession.user = { id: "u1", email: "a@example.com", displayName: "User A" };
  mockSession.accessToken = "token-a";
  mockSession.status = "authenticated";
  videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4")] });
  cadenceApi.getCadence.mockResolvedValue(CADENCE);
  queueApi.listQueueSlots.mockResolvedValue({ slots: [openSlot] });
  platformsApi.getTikTokConnection.mockResolvedValue(CONNECTED);
  platformsApi.getInstagramConnection.mockResolvedValue({
    platform: "instagram", connected: false, status: "DISCONNECTED", account_label: null, connect_available: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("navigation keeps server state", () => {
  it("Library → Queue → Library renders the cached list immediately, with one videos request in total", async () => {
    const app = renderWithProviders(<LibraryPage />);
    expect(screen.getByText("Loading your videos…")).toBeInTheDocument(); // first load only
    await screen.findByText("first.mp4");

    app.rerenderWithProviders(<QueuePage />);
    expect(within(await unscheduledSection()).getByText("first.mp4")).toBeInTheDocument(); // same cache entry

    app.rerenderWithProviders(<LibraryPage />);
    expect(screen.getByText("first.mp4")).toBeInTheDocument(); // synchronously: no spinner, no blank frame
    expect(screen.queryByText("Loading your videos…")).not.toBeInTheDocument();
    expect(videosApi.listVideos).toHaveBeenCalledTimes(1);
  });

  it("shows cached data while a background refresh is still in flight, then the fresh data", async () => {
    const app = renderWithProviders(<LibraryPage />);
    await screen.findByText("first.mp4");
    app.rerenderWithProviders(<div>elsewhere</div>);

    let finish: (value: unknown) => void = () => {};
    videosApi.listVideos.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    await act(() => app.queryClient.invalidateQueries({ queryKey: queryKeys.videos("u1") }));

    app.rerenderWithProviders(<LibraryPage />);
    expect(screen.getByText("first.mp4")).toBeInTheDocument();
    expect(screen.queryByText("Loading your videos…")).not.toBeInTheDocument();

    await act(async () => finish({ videos: [video(1, "first.mp4"), video(2, "second.mp4")] }));
    expect(await screen.findByText("second.mp4")).toBeInTheDocument();
  });

  it("makes one request per resource even when several screens and StrictMode mount it at once", async () => {
    renderWithProviders(
      <StrictMode>
        <AppHomePage />
        <LibraryPage />
      </StrictMode>,
    );
    await screen.findByText("first.mp4");
    expect(videosApi.listVideos).toHaveBeenCalledTimes(1);
    expect(platformsApi.getTikTokConnection).toHaveBeenCalledTimes(1);
    expect(cadenceApi.getCadence).toHaveBeenCalledTimes(1);
  });
});

describe("errors keep the last good data", () => {
  it("a failed background refresh keeps the list and shows an actionable notice", async () => {
    const { ApiError } = await import("@/lib/api/client");
    const app = renderWithProviders(<LibraryPage />);
    await screen.findByText("first.mp4");

    videosApi.listVideos.mockRejectedValue(new ApiError("Server unavailable", { status: 503 }));
    await act(() => app.queryClient.invalidateQueries({ queryKey: queryKeys.videos("u1") }));

    expect(await screen.findByText("Couldn't refresh your videos. Showing the last list loaded.")).toBeInTheDocument();
    expect(screen.getByText("first.mp4")).toBeInTheDocument();

    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4"), video(3, "third.mp4")] });
    await userEvent.setup().click(screen.getByRole("button", { name: /try again|retry/i }));
    expect(await screen.findByText("third.mp4")).toBeInTheDocument();
  });

  it("a first-load failure is shown with Retry instead of a blank or fake page (Home)", async () => {
    const { ApiError } = await import("@/lib/api/client");
    platformsApi.getTikTokConnection.mockRejectedValue(new ApiError("Server unavailable", { status: 503 }));
    renderWithProviders(<AppHomePage />);
    expect(await screen.findByText("Could not load your setup status.")).toBeInTheDocument();
  });
});

describe("mutations update every view that shows the data", () => {
  it("assigning in Queue updates the video's Library status without a reload", async () => {
    const app = renderWithProviders(<QueuePage />);
    await within(await unscheduledSection()).findByText("first.mp4");

    queueApi.assignNextOpenSlot.mockResolvedValue({ ...openSlot, status: "ASSIGNED" });
    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4", "SCHEDULED", 50)] });
    await userEvent.setup().click(screen.getByRole("button", { name: "Assign to next available slot" }));
    await waitFor(() => expect(queueApi.listQueueSlots).toHaveBeenCalledTimes(2));

    app.rerenderWithProviders(<LibraryPage />);
    const row = within(screen.getByText("first.mp4").closest("li") as HTMLElement);
    expect(row.getByText("Scheduled")).toBeInTheDocument();
    expect(videosApi.listVideos).toHaveBeenCalledTimes(2);
  });

  it("disconnecting TikTok in Settings updates Home without refetching the status", async () => {
    platformsApi.disconnectTikTok.mockResolvedValue(DISCONNECTED);
    const app = renderWithProviders(<SettingsPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Disconnect" }));
    await within(await screen.findByRole("group", { name: "tiktok connection" })).findByText("Not connected");

    app.rerenderWithProviders(<AppHomePage />);
    expect(await screen.findByRole("link", { name: /connect in settings/i })).toBeInTheDocument();
    expect(platformsApi.getTikTokConnection).toHaveBeenCalledTimes(1);
  });

  it("deleting in Library also removes the video from the Queue's unscheduled list", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4"), video(2, "doomed.mp4")] });
    const app = renderWithProviders(<LibraryPage />);
    await screen.findByText("doomed.mp4");

    videosApi.deleteVideo.mockResolvedValue(undefined);
    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4")] });
    const user = userEvent.setup();
    const row = within(screen.getByText("doomed.mp4").closest("li") as HTMLElement);
    await user.click(row.getByRole("button", { name: "Delete" }));
    await user.click(row.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(screen.queryByText("doomed.mp4")).not.toBeInTheDocument());

    app.rerenderWithProviders(<QueuePage />);
    const unscheduled = within(await unscheduledSection());
    expect(unscheduled.getByText("first.mp4")).toBeInTheDocument();
    expect(unscheduled.queryByText("doomed.mp4")).not.toBeInTheDocument();
  });
});

describe("UI state that survives navigation", () => {
  it("keeps the Library filter across Library → Queue → Library", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "first.mp4"), video(2, "posted.mp4", "PUBLISHED", 9)] });
    const app = renderWithProviders(<LibraryPage />);
    await screen.findByText("posted.mp4");
    await userEvent.setup().click(screen.getByRole("tab", { name: /^Published/ }));
    expect(screen.queryByText("first.mp4")).not.toBeInTheDocument();

    app.rerenderWithProviders(<QueuePage />);
    app.rerenderWithProviders(<LibraryPage />);
    expect(screen.getByRole("tab", { name: /^Published/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("first.mp4")).not.toBeInTheDocument();
  });

  it("keeps an unsaved cadence edit across navigation, and drops it once saved", async () => {
    const app = renderWithProviders(<QueuePage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Friday" }));
    await user.click(screen.getByRole("button", { name: "Add posting time for Friday" }));
    const input = screen.getByLabelText("New time for Friday");
    await user.clear(input);
    await user.type(input, "18:00");
    await user.click(screen.getByRole("button", { name: "Add time to Friday" }));

    app.rerenderWithProviders(<LibraryPage />);
    app.rerenderWithProviders(<QueuePage />);
    expect(screen.getByText("6:00 PM")).toBeInTheDocument(); // the draft came back

    const saved = { ...CADENCE, posting_times: [...CADENCE.posting_times, { weekday: "friday", posting_time: "18:00" }] };
    cadenceApi.saveCadence.mockResolvedValue(saved);
    await user.click(screen.getByRole("button", { name: /save schedule/i }));
    await waitFor(() => expect(cadenceApi.saveCadence).toHaveBeenCalledTimes(1));
    expect(cadenceApi.saveCadence.mock.calls[0][1].posting_times).toContainEqual({ weekday: "friday", posting_time: "18:00" });
    await waitFor(() => expect(queueApi.listQueueSlots).toHaveBeenCalledTimes(2)); // Queue refreshed after save

    app.rerenderWithProviders(<AppHomePage />);
    expect(await screen.findByText("America/New_York")).toBeInTheDocument(); // schedule step done
    expect(cadenceApi.getCadence).toHaveBeenCalledTimes(1); // Home used the saved value from the cache
  });

  it("keeps the Queue's calendar view across navigation", async () => {
    const app = renderWithProviders(<QueuePage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Calendar" }));
    app.rerenderWithProviders(<LibraryPage />);
    app.rerenderWithProviders(<QueuePage />);
    expect(screen.getByRole("button", { name: "Calendar" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("user isolation", () => {
  it("never shows the previous user's cached data or UI state after an account switch", async () => {
    videosApi.listVideos.mockResolvedValue({ videos: [video(1, "a-private.mp4", "PUBLISHED", 9)] });
    const app = renderWithProviders(<LibraryPage />);
    await screen.findByText("a-private.mp4");
    await userEvent.setup().click(screen.getByRole("tab", { name: /^Published/ }));

    let finish: (value: unknown) => void = () => {};
    videosApi.listVideos.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mockSession.user = { id: "u2", email: "b@example.com", displayName: "User B" };
    mockSession.accessToken = "token-b";
    app.rerenderWithProviders(<LibraryPage />);

    expect(screen.queryByText("a-private.mp4")).not.toBeInTheDocument();
    expect(screen.getByText("Loading your videos…")).toBeInTheDocument();
    await act(async () => finish({ videos: [video(5, "b-own.mp4")] }));
    expect(await screen.findByText("b-own.mp4")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^All/ })).toHaveAttribute("aria-selected", "true"); // filter reset
    expect(app.queryClient.getQueryCache().findAll({ queryKey: queryKeys.user("u1") })).toHaveLength(0);
  });

  it("signing out clears every cached query", async () => {
    const app = renderWithProviders(<AppHomePage />);
    await screen.findByText("1 video");

    mockSession.user = null;
    mockSession.accessToken = null;
    mockSession.status = "unauthenticated";
    app.rerenderWithProviders(<div>signed out</div>);
    await waitFor(() => expect(app.queryClient.getQueryCache().getAll()).toHaveLength(0));
  });
});
