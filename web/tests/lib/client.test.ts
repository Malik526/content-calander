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
  vi.unstubAllEnvs();
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

  it("uses the real backend's {detail} field, not just {message} — FastAPI's actual error shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Token verification failed: Signature has expired" }, { status: 401, ok: false })),
    );
    const error = (await apiRequest("/api/platforms/tiktok/status").catch((e) => e)) as ApiError;
    expect(error.status).toBe(401);
    expect(error.message).toBe("Token verification failed: Signature has expired");
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

  it("passes a FormData body straight through, unstringified, with no manual Content-Type", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const formData = new FormData();
    formData.append("files", new File(["x"], "x.mp4"));

    await apiRequest("/api/videos", { method: "POST", body: formData });

    const [, init = {}] = fetchMock.mock.calls[0];
    expect(init.body).toBe(formData); // not JSON.stringify'd
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("still JSON-stringifies a plain object body and sets Content-Type: application/json", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/me", { method: "POST", body: { a: 1 } });

    const [, init = {}] = fetchMock.mock.calls[0];
    expect(init.body).toBe(JSON.stringify({ a: 1 }));
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("resolves to undefined for a 204 No Content response without trying to parse a body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 204,
        json: async () => {
          throw new SyntaxError("Unexpected end of JSON input"); // a real fetch Response would throw here too
        },
      })) as unknown as typeof fetch,
    );
    await expect(apiRequest("/api/videos/1", { method: "DELETE" })).resolves.toBeUndefined();
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

describe("apiRequest — NEXT_PUBLIC_API_BASE_URL trailing-slash normalization", () => {
  // API_BASE_URL is resolved once at module load, so each case needs its
  // own fresh module instance (vi.resetModules + a dynamic import) after
  // stubbing the env var — a plain module-level import can't be re-read
  // per test.
  it("does not double the slash when the base URL is configured with a trailing slash", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com/");
    vi.resetModules();
    const fetchMock = vi.fn(async () => jsonResponse({ connected: false }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiRequest: freshApiRequest } = await import("@/lib/api/client");
    await freshApiRequest("/api/platforms/tiktok/status");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/platforms/tiktok/status",
      expect.anything(),
    );
  });

  it("still works correctly when the base URL has no trailing slash", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.com");
    vi.resetModules();
    const fetchMock = vi.fn(async () => jsonResponse({ connected: false }));
    vi.stubGlobal("fetch", fetchMock);

    const { apiRequest: freshApiRequest } = await import("@/lib/api/client");
    await freshApiRequest("/api/platforms/tiktok/status");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/platforms/tiktok/status",
      expect.anything(),
    );
  });
});
