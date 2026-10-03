import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueuePage from "@/app/app/queue/page";
import type { CaptionResponse } from "@/lib/api/types";
import { renderWithProviders } from "@/tests/test-utils";

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
  retrySlotPublication: vi.fn(),
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
    can_generate: false, editable: true, hashtags: [], ...overrides,
  };
}

// Milestone 3.11 publish-state fields, defaulted for slots that don't care.
const NO_PUBLISH_DETAIL = {
  reason_code: null, message: null, action_hint: null, published_at: null, can_unassign: false, publications: [],
  can_retry: false, retry_requires_confirmation: false,
};

const OPEN_SLOT = {
  id: 1, scheduled_at: "2026-10-05T09:00:00", timezone: "America/New_York",
  status: "OPEN", display_status: "OPEN", assigned_video: null, platform_post_status: null, ...NO_PUBLISH_DETAIL,
};

const ASSIGNED_SLOT = {
  id: 2, scheduled_at: "2026-10-06T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "SCHEDULED", ...NO_PUBLISH_DETAIL, can_unassign: true,
  assigned_video: { id: 5, original_filename: "clip.mp4", caption: captionFor(5, "Saved caption") }, platform_post_status: "PENDING",
};

const PUBLISHED_SLOT = {
  id: 3, scheduled_at: "2026-10-07T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "PUBLISHED", ...NO_PUBLISH_DETAIL, published_at: "2026-10-07T13:00:05+00:00",
  assigned_video: { id: 6, original_filename: "posted.mp4", caption: captionFor(6, "Posted caption", { editable: false }) }, platform_post_status: "PUBLISHED",
};

const FAILED_SLOT = {
  id: 4, scheduled_at: "2026-10-08T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "FAILED", ...NO_PUBLISH_DETAIL,
  reason_code: "AUTH_REQUIRED", message: "TikTok connection needs to be renewed. Reconnect your account in Settings.",
  action_hint: "RECONNECT_ACCOUNT",
  assigned_video: { id: 7, original_filename: "failed.mp4", caption: captionFor(7, "Caption") }, platform_post_status: "FAILED",
};

const ATTENTION_SLOT = {
  id: 5, scheduled_at: "2026-10-09T09:00:00", timezone: "America/New_York",
  status: "ASSIGNED", display_status: "NEEDS_ATTENTION", ...NO_PUBLISH_DETAIL,
  reason_code: "PUBLISH_UNCONFIRMED", message: "Publishing status could not be confirmed.",
  assigned_video: { id: 8, original_filename: "unsure.mp4", caption: captionFor(8, "Caption", { editable: false }) }, platform_post_status: "PUBLISHING",
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

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText(/Oct/)).toBeInTheDocument());
    expect(screen.getByText("Open")).toBeInTheDocument();
  });

  it("shows an assigned slot with its video's filename", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("clip.mp4")).toBeInTheDocument());
    expect(screen.getByText("Scheduled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove from schedule" })).toBeInTheDocument();
  });

  it("does not offer Remove from schedule for a published slot", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [PUBLISHED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("posted.mp4")).toBeInTheDocument());
    expect(screen.getByText("Published")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove from schedule" })).not.toBeInTheDocument();
  });

  it("lists unscheduled videos with an assign-to-next-slot action", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("raw.mp4")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Assign to next available slot" })).toBeInTheDocument();
  });

  it("assigns the next available slot when clicked", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [OPEN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });
    queueApi.assignNextOpenSlot.mockResolvedValue({ ...OPEN_SLOT, status: "ASSIGNED", display_status: "SCHEDULED", assigned_video: { id: 10, original_filename: "raw.mp4", caption: captionFor(10, null) } });

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Assign to next available slot" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Assign to next available slot" }));

    await waitFor(() => expect(queueApi.assignNextOpenSlot).toHaveBeenCalledWith("real-token", 10));
  });

  it("manually assigns a chosen video to a specific OPEN slot", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [OPEN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [UNASSIGNED_VIDEO] });
    queueApi.assignVideoToSlot.mockResolvedValue({ ...OPEN_SLOT, status: "ASSIGNED" });

    renderWithProviders(<QueuePage />);
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

    renderWithProviders(<QueuePage />);
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

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Calendar" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Calendar" }));

    expect(screen.getByText("Select a date's slot to see its details.")).toBeInTheDocument();

    const dot = screen.getByRole("button", { name: /scheduled/i, pressed: false });  // 3.11: "assigned" renamed
    await user.click(dot);

    expect(screen.getByText("clip.mp4")).toBeInTheDocument();
  });

  it("shows an assigned video's saved caption", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("Saved caption"));
    expect(screen.getByText("Written by you")).toBeInTheDocument();
  });

  it("saves an edited caption and keeps the saved value", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    captionsApi.saveCaption.mockResolvedValue(captionFor(5, "New caption #fyp"));

    renderWithProviders(<QueuePage />);
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

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("Posted caption"));
    expect(screen.getByLabelText("Caption")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Save caption" })).not.toBeInTheDocument();
  });

  it("explains a failed slot with sanitized copy and a reconnect link, without offering removal", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [FAILED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("Failed")).toBeInTheDocument());
    expect(screen.getByText("TikTok connection needs to be renewed. Reconnect your account in Settings.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Reconnect in Settings" })).toHaveAttribute("href", "/app/settings");
    expect(screen.queryByRole("button", { name: "Remove from schedule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("shows Needs attention with its explanation", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [ATTENTION_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("Needs attention")).toBeInTheDocument());
    expect(screen.getByText("Publishing status could not be confirmed.")).toBeInTheDocument();
  });

  it("shows when a published slot was published", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [PUBLISHED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText(/^Published .+/)).toBeInTheDocument());
  });

  it("never shows an unrecognized status as its raw value or as success", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [{ ...ATTENTION_SLOT, display_status: "SOMETHING_NEW", message: null }] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("Needs attention")).toBeInTheDocument());
    expect(screen.queryByText("SOMETHING_NEW")).not.toBeInTheDocument();
  });

  it("shows the same status in list and calendar views", async () => {
    const now = new Date();
    const inCurrentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-15T09:00:00`;
    queueApi.listQueueSlots.mockResolvedValue({ slots: [{ ...FAILED_SLOT, scheduled_at: inCurrentMonth }] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByText("Failed")).toBeInTheDocument());
    const listMessage = screen.getByText(FAILED_SLOT.message).textContent;

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Calendar" }));
    const dot = screen.getByRole("button", { name: /, failed$/ });
    await user.click(dot);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(FAILED_SLOT.message).textContent).toBe(listMessage);
  });

  it("shows an error state with retry when loading the queue fails", async () => {
    const { ApiError } = await import("@/lib/api/client");
    queueApi.listQueueSlots.mockRejectedValueOnce(new ApiError("Could not reach the server."));
    queueApi.listQueueSlots.mockResolvedValueOnce({ slots: [] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("Could not reach the server.")).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText("No slots in this window yet.")).toBeInTheDocument());
  });

  // --- Milestone 3.14: recovery (Retry) -------------------------------------------

  const RETRYABLE_FAILED_SLOT = { ...FAILED_SLOT, can_retry: true };
  const UNKNOWN_SLOT = {
    ...ATTENTION_SLOT, id: 6, reason_code: "OUTCOME_UNKNOWN", message: "We couldn't confirm whether this was published.",
    platform_post_status: "UNKNOWN", can_retry: true, retry_requires_confirmation: true,
  };

  it("does not offer Retry when the backend says the post can't be retried", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [FAILED_SLOT, PUBLISHED_SLOT, ASSIGNED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });

    renderWithProviders(<QueuePage />);

    await waitFor(() => expect(screen.getByText("failed.mp4")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("retries a failed post directly and reloads the queue", async () => {
    queueApi.listQueueSlots
      .mockResolvedValueOnce({ slots: [RETRYABLE_FAILED_SLOT] })
      .mockResolvedValue({ slots: [{ ...RETRYABLE_FAILED_SLOT, display_status: "SCHEDULED", can_retry: false }] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    queueApi.retrySlotPublication.mockResolvedValue({});

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(queueApi.retrySlotPublication).toHaveBeenCalledWith("real-token", 4, { confirmNotPublished: false });
    await waitFor(() => expect(screen.getByText("Scheduled")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("asks for confirmation before retrying a post whose outcome is unknown", async () => {
    queueApi.listQueueSlots.mockResolvedValue({ slots: [UNKNOWN_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    queueApi.retrySlotPublication.mockResolvedValue({});

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(queueApi.retrySlotPublication).not.toHaveBeenCalled();
    expect(screen.getByText(/retrying will post it a second time/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/retrying will post it a second time/)).not.toBeInTheDocument();
    expect(queueApi.retrySlotPublication).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    await user.click(screen.getByRole("button", { name: /it isn't posted — retry/i }));
    expect(queueApi.retrySlotPublication).toHaveBeenCalledWith("real-token", 6, { confirmNotPublished: true });
  });

  it("shows the backend's message when a retry is refused", async () => {
    const { ApiError } = await import("@/lib/api/client");
    queueApi.listQueueSlots.mockResolvedValue({ slots: [RETRYABLE_FAILED_SLOT] });
    videosApi.listVideos.mockResolvedValue({ videos: [] });
    queueApi.retrySlotPublication.mockRejectedValue(
      new ApiError("This post changed while retrying. Refresh and try again.", { status: 409, reasonCode: "CONCURRENT_UPDATE" }),
    );

    renderWithProviders(<QueuePage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(screen.getByText("This post changed while retrying. Refresh and try again.")).toBeInTheDocument(),
    );
  });
});
