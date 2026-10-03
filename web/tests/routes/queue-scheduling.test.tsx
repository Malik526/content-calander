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
  retrySlotPublication: vi.fn(),
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

/** Opens a day's draft picker, enters `time` (HH:MM), and clicks Add. */
async function addTime(user: ReturnType<typeof userEvent.setup>, day: string, time: string) {
  await user.click(screen.getByRole("button", { name: `Add posting time for ${day}` }));
  const input = screen.getByLabelText(`New time for ${day}`);
  await user.clear(input);
  await user.type(input, time);
  await user.click(screen.getByRole("button", { name: `Add time to ${day}` }));
}

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

  // Milestone 3.14 follow-up: enabling a day must not seed a phantom 09:00
  // time — it starts empty until the user explicitly adds one.
  it("enables a weekday with zero committed times and no 09:00", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Wednesday" })).not.toBeChecked());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Wednesday" }));

    expect(screen.getByRole("checkbox", { name: "Wednesday" })).toBeChecked();
    expect(screen.queryByText("9:00 AM")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove Wednesday/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("New time for Wednesday")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add posting time for Wednesday" })).toBeInTheDocument();
  });

  it("opens the draft picker from Add posting time without committing its value", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Monday" })).not.toBeChecked());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));
    await user.click(screen.getByRole("button", { name: "Add posting time for Monday" }));

    // The draft picker may start at 09:00, but that is not a committed time.
    expect(screen.getByLabelText("New time for Monday")).toHaveValue("09:00");
    expect(screen.queryByText("9:00 AM")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove Monday/ })).not.toBeInTheDocument();
  });

  it("adds nothing when the picker on a newly enabled day is cancelled", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    cadenceApi.saveCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: false, posting_times: [],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Monday" })).not.toBeChecked());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));
    await user.click(screen.getByRole("button", { name: "Add posting time for Monday" }));
    await user.click(screen.getByRole("button", { name: "Cancel adding time to Monday" }));

    expect(screen.queryByLabelText("New time for Monday")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove Monday/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /save schedule/i }));
    await waitFor(() => expect(cadenceApi.saveCadence).toHaveBeenCalledTimes(1));
    expect(cadenceApi.saveCadence.mock.calls[0][1].posting_times).toEqual([]);
  });

  it("builds a newly enabled day from only explicitly added times and saves exactly those", async () => {
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    cadenceApi.saveCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: false,
      posting_times: [{ weekday: "monday", posting_time: "18:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Monday" })).not.toBeChecked());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));

    await addTime(user, "Monday", "14:30");
    expect(screen.getAllByRole("button", { name: /^Remove Monday/ })).toHaveLength(1);
    expect(screen.getByText("2:30 PM")).toBeInTheDocument();
    expect(screen.queryByText("9:00 AM")).not.toBeInTheDocument();

    await addTime(user, "Monday", "18:00");
    expect(screen.getAllByRole("button", { name: /^Remove Monday/ })).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Remove Monday 2:30 PM" }));
    expect(screen.getAllByRole("button", { name: /^Remove Monday/ })).toHaveLength(1);
    expect(screen.getByText("6:00 PM")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /save schedule/i }));
    await waitFor(() => expect(cadenceApi.saveCadence).toHaveBeenCalledTimes(1));
    expect(cadenceApi.saveCadence.mock.calls[0][1].posting_times).toEqual([
      { weekday: "monday", posting_time: "18:00" },
    ]);
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

  // Milestone 3.14 UX fix: the draft time input only appears after the user
  // explicitly clicks "+ Add posting time" — no phantom default value visible
  // alongside already-configured times.
  it("does not show a draft time input for already-configured days until Add posting time is clicked", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("9:00 AM")).toBeInTheDocument());

    // Draft input must NOT be visible until the user clicks "+ Add posting time".
    expect(screen.queryByLabelText("New time for Monday")).not.toBeInTheDocument();
  });

  it("adds a second time to an already-active day", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("9:00 AM")).toBeInTheDocument());

    const user = userEvent.setup();
    // First open the draft form by clicking the trigger; the input is only
    // visible after this explicit action (Milestone 3.14 UX fix).
    await user.click(screen.getByRole("button", { name: "Add posting time for Monday" }));
    const newTimeInput = screen.getByLabelText("New time for Monday");
    await user.clear(newTimeInput);
    await user.type(newTimeInput, "18:00");
    await user.click(screen.getByRole("button", { name: "Add time to Monday" }));

    expect(screen.getByText("9:00 AM")).toBeInTheDocument();
    expect(screen.getByText("6:00 PM")).toBeInTheDocument();
  });

  it("cancels an in-progress add without committing the time", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("9:00 AM")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add posting time for Monday" }));
    expect(screen.getByLabelText("New time for Monday")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel adding time to Monday" }));
    expect(screen.queryByLabelText("New time for Monday")).not.toBeInTheDocument();
    expect(screen.getAllByText("9:00 AM")).toHaveLength(1); // no phantom duplicate
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
    await addTime(user, "Friday", "09:00");
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

  // Milestone 3.14 regression: saving used to leave the Queue stale until a
  // manual browser refresh, because QueueBoard only loaded on mount.
  it("shows the slots a cadence save generated without a page refresh", async () => {
    const generatedSlot = {
      id: 1, scheduled_at: "2026-10-05T09:00:00", timezone: "America/New_York", status: "OPEN",
      display_status: "OPEN", reason_code: null, message: null, action_hint: null, published_at: null,
      can_unassign: false, can_retry: false, retry_requires_confirmation: false, assigned_video: null,
      platform_post_status: null, publications: [],
    };
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    cadenceApi.saveCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    queueApi.listQueueSlots.mockResolvedValueOnce({ slots: [] }).mockResolvedValue({ slots: [generatedSlot] });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByText("No slots in this window yet.")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));
    await addTime(user, "Monday", "09:00");
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    await waitFor(() => expect(screen.getByText("Open")).toBeInTheDocument());
    expect(screen.queryByText("No slots in this window yet.")).not.toBeInTheDocument();
    expect(queueApi.listQueueSlots).toHaveBeenCalledTimes(2);
  });

  it("does not reload the queue when saving the cadence fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
    cadenceApi.saveCadence.mockRejectedValue(new ApiError("Invalid timezone."));

    render(<QueuePage />);
    await waitFor(() => expect(queueApi.listQueueSlots).toHaveBeenCalledTimes(1));

    await userEvent.setup().click(screen.getByRole("button", { name: /save schedule/i }));

    await waitFor(() => expect(screen.getByText("Invalid timezone.")).toBeInTheDocument());
    expect(queueApi.listQueueSlots).toHaveBeenCalledTimes(1);
  });
});
