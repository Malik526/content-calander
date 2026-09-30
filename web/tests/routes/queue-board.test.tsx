import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueuePage from "@/app/app/queue/page";
import type { CaptionResponse } from "@/lib/api/types";

/**
 * Queue's real "Queue" section (Milestone 3.9: Queue + Calendar
 * Functionality) — components/app/QueueBoard.tsx and its children
 * (QueueList, QueueCalendarMonth, QueueSlotCard). lib/api/queue.ts and
 * lib/api/videos.ts are mocked directly, same pattern as
 * tests/routes/queue-scheduling.test.tsx. lib/api/cadence.ts is also
 * mocked here (default resolved to "not configured") purely so the
 * Posting rhythm section on the same page doesn't produce an unrelated
 * error alert — its own behavior is covered by
 * tests/routes/queue-scheduling.test.tsx.
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

const captionsApi = vi.hoisted(() => ({
  getCaption: vi.fn(),
  saveCaption: vi.fn(),
  generateCaption: vi.fn(),
}));
vi.mock("@/lib/api/captions", () => captionsApi);

function captionFor(videoId: number, text: string | null, overrides: Partial<CaptionResponse> = {}): CaptionResponse {
  return {
    video_id: videoId, caption_text: text, provenance: text ? "MANUAL" : "NONE",
    can_generate: false, editable: true, ...overrides,
  };
}

const OPEN_SLOT = {
  id: 1, scheduled_at: "2026-10-05T09:00:00", timezone: "America/New_York",
  status: "OPEN", display_status: "OPEN", assigned_video: null, platform_post_status: null,
};

const ASSIGNED_SLOT = {
  id: 2, scheduled_at: "2026-10-06T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "ASSIGNED",
  assigned_video: { id: 5, original_filename: "clip.mp4", caption: captionFor(5, "Saved caption") }, platform_post_status: "PENDING",
};

const PUBLISHED_SLOT = {
  id: 3, scheduled_at: "2026-10-07T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "PUBLISHED",
  assigned_video: { id: 6, original_filename: "posted.mp4", caption: captionFor(6, "Posted caption", { editable: false }) }, platform_post_status: "PUBLISHED",
};

const UNASSIGNED_VIDEO = {
  id: 10, original_filename: "raw.mp4", status: "DISCOVERED", file_size_bytes: 100, created_at: "2026-01-01T00:00:00",
  assigned_slot_id: null,
};

beforeEach(() => {
  cadenceApi.getCadence.mockResolvedValue({ configured: false, timezone: null, is_active: false, posting_times: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("QueuePage — Queue (list/calendar, assign/unassign)", () => {
  it("shows OPEN slots with no assigned video", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [OPEN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByText(/Oct/)).toBeInTheDocument());
    expect(screen.getByText("Open")).toBeInTheDocument();
  });

  it("shows an assigned slot with its video's filename", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());
    expect(screen.getByText("Assigned")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove from schedule" })).toBeInTheDocument();
  });

  it("does not offer Remove from schedule for a published slot", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [PUBLISHED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByText("posted.mp4")).toBeInTheDocument());
    expect(screen.getByText("Published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove from schedule" })).not.toBeInTheDocument();
  });

  it("lists unscheduled videos with an assign-to-next-slot action", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByText("raw.mp4")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Assign to next available slot" })).toBeInTheDocument();
  });

  it("assigns the next available slot when clicked", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [OPEN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });
    queueApi.assignNextOpenSlot.mockResolvedValue({ ...OPEN_SLOT, status: "ASSIGNED", display_status: "ASSIGNED", assigned_video: { id: 10, original_filename: "raw.mp4", caption: captionFor(10, null) } });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Assign to next available slot" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Assign to next available slot" }));

    await waitFor(() => expect(queueApi.assignNextOpenSlot).toHaveBeenCalledWith("real-token", 10));
  });

  it("manually assigns a chosen video to a specific OPEN slot", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [OPEN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });
    queueApi.assignVideoToSlot.mockResolvedValue({ ...OPEN_SLOT, status: "ASSIGNED" });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("combobox", { name: /video to assign/i })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: /video to assign/i }), "10");
    await user.click(screen.getByRole("button", { name: "Assign" }));

    await waitFor(() => expect(queueApi.assignVideoToSlot).toHaveBeenCalledWith("real-token", 1, 10));
  });

  it("removes an assigned video from its schedule, keeping the video", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    queueApi.unassignSlot.mockResolvedValue({ ...ASSIGNED_SLOT, status: "OPEN", display_status: "OPEN", assigned_video: null });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove from schedule" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Remove from schedule" }));

    await waitFor(() => expect(queueApi.unassignSlot).toHaveBeenCalledWith("real-token", 2));
  });

  it("switches to calendar view and selects a slot to see its detail", async () => {
    // QueueCalendarMonth's default view is the real current month
    // (QueueBoard's monthCursor starts at `new Date()`) — the slot must
    // fall within it, or its dot simply wouldn't be in the rendered grid
    // at all, regardless of what window listQueueSlots was mocked to
    // return for. Day 15 avoids any month-boundary edge case.
    const now = new Date();
    const inCurrentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-15T09:00:00`;
    queueApi.listQueueSlots.mockResolvedValue({ slots: [{ ...ASSIGNED_SLOT, scheduled_at: inCurrentMonth }] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Calendar" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Calendar" }));

    expect(screen.getByText("Select a date's slot to see its details.")).toBeInTheDocument();

    const dot = screen.getByRole("button", { name: /assigned/i, pressed: false });
    await user.click(dot);

    expect(screen.getByText("clip.mp4")).toBeInTheDocument();
  });

  it("shows an assigned video's saved caption", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("Saved caption"));
    expect(screen.getByText("Written by you")).toBeInTheDocument();
  });

  it("saves an edited caption and keeps the saved value", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    captionsApi.saveCaption.mockResolvedValue(captionFor(5, "New caption #fyp"));

    render(<QueuePage />);
    await waitFor(() => expect(screen.getByLabelText("Caption")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.clear(screen.getByLabelText("Caption"));
    await user.type(screen.getByLabelText("Caption"), "New caption #fyp");
    await user.click(screen.getByRole("button", { name: "Save caption" }));

    await waitFor(() => expect(captionsApi.saveCaption).toHaveBeenCalledWith("real-token", 5, "New caption #fyp"));
    await waitFor(() => expect(screen.getByText(/Saved ·/)).toBeInTheDocument());
    expect(screen.getByLabelText("Caption")).toHaveValue("New caption #fyp");
    expect(screen.getByRole("button", { name: "Save caption" })).toBeDisabled();
  });

  it("shows a published video's caption read-only", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [PUBLISHED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("Posted caption"));
    expect(screen.getByLabelText("Caption")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save caption" })).not.toBeInTheDocument();
  });

  it("shows an error state with retry when loading the queue fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    queueApi.listQueueSlots.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    queueApi.listQueueSlots.mockResolvedValueOnce({ slots: [] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    render(<QueuePage />);

    await waitFor(() => expect(screen.getByText("Could not reach the server.")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("No slots in this window yet.")).toBeInTheDocument());
  });
});
