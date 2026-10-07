import { afterEach, describe, expect, it } from "vitest";
import { instagramCallbackNotice, takeInstagramCallbackOutcome } from "@/lib/instagramCallback";

/** Milestone 4.1 — reading and clearing the Instagram callback outcome. */

afterEach(() => {
  window.history.replaceState({}, "", "/app/settings");
});

describe("lib/instagramCallback.ts", () => {
  it("maps each backend outcome to a clear message", () => {
    expect(instagramCallbackNotice("connected")).toEqual({ tone: "success", message: "Instagram connected." });
    for (const outcome of ["denied", "invalid_state", "expired_state", "exchange_failed", "unavailable"]) {
      expect(instagramCallbackNotice(outcome).tone).toBe("error");
    }
    expect(instagramCallbackNotice("denied").message).toMatch(/denied or cancelled/i);
  });

  it("never echoes an unrecognized outcome", () => {
    const notice = instagramCallbackNotice("<script>alert(1)</script>");
    expect(notice).toEqual({ tone: "error", message: "Something went wrong connecting Instagram." });
    expect(instagramCallbackNotice("toString").message).toBe("Something went wrong connecting Instagram.");
  });

  it("takes the outcome and removes only that parameter", () => {
    window.history.replaceState({}, "", "/app/settings?tab=platforms&instagram=connected#cards");

    expect(takeInstagramCallbackOutcome()).toBe("connected");
    expect(window.location.search).toBe("?tab=platforms");
    expect(window.location.hash).toBe("#cards");
    expect(takeInstagramCallbackOutcome()).toBeNull();
  });

  it("leaves the URL alone when there is no outcome", () => {
    window.history.replaceState({}, "", "/app/settings?tiktok=denied");
    expect(takeInstagramCallbackOutcome()).toBeNull();
    expect(window.location.search).toBe("?tiktok=denied");
  });
});
