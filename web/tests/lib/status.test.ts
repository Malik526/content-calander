import { describe, expect, it } from "vitest";
import { formatScheduledAt, libraryFilterFor, platformPostStatusPresentation, presentActionHint, presentQueueStatus } from "@/lib/status";
import type { PlatformPostStatus } from "@/lib/api/types";

describe("platformPostStatusPresentation", () => {
  it("maps every backend PlatformPostStatus to a human label and tone", () => {
    const statuses: PlatformPostStatus[] = ["pending", "publishing", "published", "failed"];
    for (const status of statuses) {
      const presentation = platformPostStatusPresentation[status];
      expect(presentation.label).not.toBe(status);
      expect(presentation.label.length).toBeGreaterThan(0);
      expect(["pending", "progress", "success", "danger", "attention"]).toContain(presentation.tone);
    }
  });
});

describe("formatScheduledAt", () => {
  it("returns 'Not scheduled' for null", () => {
    expect(formatScheduledAt(null)).toBe("Not scheduled");
  });

  it("returns 'Not scheduled' for an unparsable string instead of 'Invalid Date'", () => {
    expect(formatScheduledAt("not-a-date")).toBe("Not scheduled");
  });

  it("formats a valid ISO timestamp into a short human date/time", () => {
    const formatted = formatScheduledAt("2026-09-22T13:00:00Z");
    expect(formatted).not.toBe("Not scheduled");
    expect(formatted).toMatch(/Sep/);
  });
});

describe("presentQueueStatus (Milestone 3.11)", () => {
  it("labels every resolved display status", () => {
    expect(presentQueueStatus("OPEN").label).toBe("Open");
    expect(presentQueueStatus("SCHEDULED").label).toBe("Scheduled");
    expect(presentQueueStatus("PUBLISHING").label).toBe("Publishing");
    expect(presentQueueStatus("PUBLISHED").label).toBe("Published");
    expect(presentQueueStatus("FAILED").label).toBe("Failed");
    expect(presentQueueStatus("NEEDS_ATTENTION").label).toBe("Needs attention");
  });

  it("falls back to Needs attention for anything unrecognized, never success", () => {
    const fallback = presentQueueStatus("ASSIGNED");
    expect(fallback.label).toBe("Needs attention");
    expect(fallback.tone).toBe("attention");
  });
});

describe("presentActionHint (Milestone 3.11)", () => {
  it("maps hints to advisory actions and offers no retry", () => {
    expect(presentActionHint("RECONNECT_ACCOUNT")).toEqual({ label: "Reconnect in Settings", href: "/app/settings" });
    expect(presentActionHint("EDIT_CAPTION")?.label).toBe("Edit the caption below.");
    expect(presentActionHint("TRY_AGAIN_LATER")).toBeNull();
    expect(presentActionHint(null)).toBeNull();
  });
});

describe("libraryFilterFor (Milestone 3.14 final follow-up)", () => {
  it("buckets backend publish_status into Library tabs", () => {
    expect(libraryFilterFor("UNSCHEDULED")).toBe("unscheduled");
    expect(libraryFilterFor("PUBLISHED")).toBe("published");
    for (const status of ["SCHEDULED", "PUBLISHING", "FAILED", "NEEDS_ATTENTION", "SOMETHING_NEW"]) {
      expect(libraryFilterFor(status)).toBe("scheduled");
    }
  });
});
