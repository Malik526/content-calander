import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QueueItemCard } from "@/components/app/QueueItemCard";
import type { QueueItem } from "@/lib/api/types";

const item: QueueItem = {
  id: "q1",
  videoTitle: "Behind the scenes — building the scheduler",
  platform: "tiktok",
  status: "pending",
  scheduledAt: "2026-09-22T13:00:00Z",
};

describe("QueueItemCard", () => {
  it("renders the video title and a presentation label instead of the raw status enum", () => {
    render(<QueueItemCard item={item} />);
    expect(screen.getByText(item.videoTitle)).toBeInTheDocument();
    expect(screen.getByText("Scheduled")).toBeInTheDocument();
    expect(screen.queryByText("pending")).not.toBeInTheDocument();
  });

  it("shows 'Not scheduled' when scheduledAt is null", () => {
    render(<QueueItemCard item={{ ...item, scheduledAt: null }} />);
    expect(screen.getByText(/not scheduled/i)).toBeInTheDocument();
  });
});
