import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest, ApiError } from "@/lib/api/client";

/**
 * apiRequest() is the one place this shell would make a real backend
 * call (Milestone 3.5, Phase 12) — no backend exists yet, so these
 * tests exercise it directly against a stubbed global fetch rather than
 * a real server, proving every failure mode normalizes into a stable
 * ApiError shape instead of leaking a raw fetch/Response error.
 */

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("returns parsed JSON on a successful response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ id: "v1" })));
    await expect(apiRequest("/videos/v1")).resolves.toEqual({ id: "v1" });
  });

  it("normalizes a network failure into a NETWORK_ERROR ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(apiRequest("/videos")).rejects.toMatchObject({
      name: "ApiError",
      reasonCode: "NETWORK_ERROR",
    });
  });

  it("normalizes a non-2xx response with a JSON error body into an HTTP_ERROR with that message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ message: "Video not found." }, { status: 404, ok: false })),
    );
    const error = (await apiRequest("/videos/missing").catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.reasonCode).toBe("HTTP_ERROR");
    expect(error.message).toBe("Video not found.");
  });

  it("falls back to a generic message when a non-2xx response body isn't JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => {
          throw new SyntaxError("not json");
        },
      })) as unknown as typeof fetch,
    );
    const error = (await apiRequest("/videos").catch((e) => e)) as ApiError;
    expect(error.status).toBe(500);
    expect(error.message).toBe("Request failed (HTTP 500).");
  });

  it("normalizes a malformed successful response into MALFORMED_RESPONSE", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("not json");
        },
      })) as unknown as typeof fetch,
    );
    await expect(apiRequest("/videos")).rejects.toMatchObject({ reasonCode: "MALFORMED_RESPONSE" });
  });
});
