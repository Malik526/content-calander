import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import QueuePage from "@/app/app/queue/page";

/**
 * Queue's posting-cadence sections (Milestone 3.8.1) — lib/api/cadence.ts
 * is mocked directly, same pattern as tests/routes/settings-tiktok.test.tsx
 * mocking lib/api/platforms. Replaces tests/routes/settings-scheduling.test.tsx,
 * which covered this same cadence editor when it lived on the Settings page
 * (Milestone 3.8) — the editor moved to Queue and was rebuilt as a
 * full-week grid (components/app/WeeklyRhythmEditor.tsx) with a
 * human-readable upcoming-slots preview
 * (components/app/UpcomingSchedulePreview.tsx); backend behavior and the
 * lib/api/cadence.ts contract are unchanged.
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

afterEach(() => {
  vi.clearAllMocks();
});

describe("QueuePage — posting rhythm", () => {
  it("loads an existing cadence into the weekly editor", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

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
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

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
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

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
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

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
    cadenceApi.getUpcomingSlots.mockResolvedValue({ slots: [] });

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

  it("renders upcoming slots in a human-readable form, not raw ISO/OPEN", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({
      slots: [{ id: 1, scheduled_at: "2026-10-05T09:00:00", status: "OPEN", timezone: "America/New_York" }],
    });

    render(<QueuePage />);

    await waitFor(() =>
      expect(within(screen.getByRole("region", { name: "Upcoming schedule" })).queryByText("No upcoming posts scheduled yet.")).not.toBeInTheDocument(),
    );
    const upcoming = within(screen.getByRole("region", { name: "Upcoming schedule" }));
    expect(screen.queryByText("2026-10-05T09:00:00")).not.toBeInTheDocument();
    expect(upcoming.queryByText("OPEN")).not.toBeInTheDocument();
    expect(upcoming.getByText(/Oct/)).toBeInTheDocument();
    expect(upcoming.getByText(/9:00/)).toBeInTheDocument();
  });

  it("shows a real, non-OPEN slot status when one exists", async () => {
    cadenceApi.getCadence.mockResolvedValue({
      configured: true, timezone: "America/New_York", is_active: true,
      posting_times: [{ weekday: "monday", posting_time: "09:00" }],
    });
    cadenceApi.getUpcomingSlots.mockResolvedValue({
      slots: [{ id: 1, scheduled_at: "2026-10-05T09:00:00", status: "ASSIGNED", timezone: "America/New_York" }],
    });

    render(<QueuePage />);

    const upcoming = within(await screen.findByRole("region", { name: "Upcoming schedule" }));
    await waitFor(() => expect(upcoming.getByText("Assigned")).toBeInTheDocument());
  });
});
