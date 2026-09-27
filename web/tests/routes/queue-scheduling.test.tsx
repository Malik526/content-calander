import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueuePage from "@/app/app/queue/page";

/**
 * Queue's "Posting rhythm" section (Milestones 3.8/3.8.1) —
 * lib/api/cadence.ts is mocked directly, same pattern as
 * tests/routes/settings-tiktok.test.tsx mocking lib/api/platforms.
 * lib/api/queue.ts and lib/api/videos.ts are also mocked here (with
 * default-empty resolved values in beforeEach) purely so QueueBoard's own
 * independent data fetch — the real Queue section Milestone 3.9 added
 * below this one on the same page — doesn't produce unrelated error
 * alerts that make these cadence-focused tests' own assertions ambiguous,
 * the same reasoning tests/routes/settings-tiktok.test.tsx already
 * established for this component. QueueBoard's own behavior is covered by
 * tests/routes/queue-board.test.tsx.
 */

const mockSession = vi.hoisted(() => ({
  user: { id: "u1", email: "creator@example.com", displayName: "Creator" },
  accessToken: "real-token",
  status: "authenticated" as const,
  signOut: vi.fn(),
}));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

const cadenceApi = vi.hoisted(() => ({
  getCadence: vi.fn(),
  saveCadence: vi.fn(),
  getUpcomingSlots: vi.fn(),
}));
vi.mock("@/lib/api/cadence", () => cadenceApi);

const queueApi = vi.hoisted(() => ({
  listQueueSlots: vi.fn(),
  assignVideoToSlot: vi.fn(),
  assignNextOpenSlot: vi.fn(),
  unassignSlot: vi.fn(),
}));
vi.mock("@/lib/api/queue", () => queueApi);

const videosApi = vi.hoisted(() => ({
  listVideos: vi.fn(),
  uploadVideos: vi.fn(),
  deleteVideo: vi.fn(),
}));
vi.mock("@/lib/api/videos", () => videosApi);

beforeEach(() => {
  queueApi.listQueueSlots.mockResolvedValue({ slots: [] });
  videosApi.listVideos.mockResolvedValue({ videos: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("QueuePage — posting rhythm", () => {
  it("loads an existing cadence into the weekly editor", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Monday" })).toBeChecked());
    expect(screen.getByText("9:00 AM")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Tuesday" })).not.toBeChecked();
  });

  it("enables a weekday and seeds it with a default time", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Wednesday" })).not.toBeChecked());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Wednesday" }));

    expect(screen.getByRole("checkbox", { name: "Wednesday" })).toBeChecked();
    expect(screen.getByText("9:00 AM")).toBeInTheDocument();
  });

  it("disables a weekday and removes its times", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "friday", posting_time: "18:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Friday" })).toBeChecked());
    expect(screen.getByText("6:00 PM")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Friday" }));

    expect(screen.getByRole("checkbox", { name: "Friday" })).not.toBeChecked();
    expect(screen.queryByText("6:00 PM")).not.toBeInTheDocument();
  });

  it("adds a second time to an already-active day", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("9:00 AM")).toBeInTheDocument());

    const user = userEvent.setup();
    const newTimeInput = screen.getByLabelText("New time for Monday");
    await user.clear(newTimeInput);
    await user.type(newTimeInput, "18:00");
    await user.click(screen.getByRole("button", { name: "Add time to Monday" }));

    expect(screen.getByText("9:00 AM")).toBeInTheDocument();
    expect(screen.getByText("6:00 PM")).toBeInTheDocument();
  });

  it("removes an individual time from a day with multiple times", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [
        { weekday: "monday", posting_time: "09:00" },
        { weekday: "monday", posting_time: "18:00" },
      ],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("6:00 PM")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove Monday 6:00 PM" }));

    expect(screen.getByText("9:00 AM")).toBeInTheDocument();
    expect(screen.queryByText("6:00 PM")).not.toBeInTheDocument();
  });

  it("saves the complete weekly cadence in one action", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.saveCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [
        { weekday: "monday", posting_time: "09:00" },
        { weekday: "friday", posting_time: "09:00" },
      ],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("9:00 AM")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Friday" }));
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    await waitFor(() =>
      expect(cadenceApi.saveCadence).toHaveBeenCalledWith(
        "real-token",
        expect.objectContaining({
          timezone: "America/New_York",
          is_active: true,
          posting_times: expect.arrayContaining([
            { weekday: "monday", posting_time: "09:00" },
            { weekday: "friday", posting_time: "09:00" },
          ]),
        }),
      ),
    );
  });
});
