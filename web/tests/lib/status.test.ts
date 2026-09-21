import { describe, expect, it } from "vitest";
import { formatScheduledAt, platformPostStatusPresentation } from "@/lib/status";
import type { PlatformPostStatus } from "@/lib/api/types";

describe("platformPostStatusPresentation", () => {
  it("maps every backend PlatformPostStatus to a human label and tone", () => {
    const statuses: PlatformPostStatus[] = ["pending", "publishing", "published", "failed"];
    for (const status of statuses) {
      const presentation = platformPostStatusPresentation[status];
      expect(presentation.label).not.toBe(status);
      expect(presentation.label.length).toBeGreaterThan(0);
      expect(["pending", "progress", "success", "danger"]).toContain(presentation.tone);
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
