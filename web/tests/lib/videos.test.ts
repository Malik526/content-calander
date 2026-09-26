import { afterEach, describe, expect, it, vi } from "vitest";
import { listVideos, uploadVideos } from "@/lib/api/videos";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function fetchMockReturning(body: unknown) {
  return vi.fn<typeof fetch>(async () => jsonResponse(body));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lib/api/videos.ts", () => {
  it("listVideos calls GET /api/videos with the access token attached", async () => {
    const fetchMock = fetchMockReturning({ videos: [] });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listVideos("token");

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/videos");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(result.videos).toEqual([]);
  });

  it("uploadVideos POSTs a multipart FormData body with every file under the same field name", async () => {
    const fetchMock = fetchMockReturning({ results: [] });
    vi.stubGlobal("fetch", fetchMock);
    const fileA = new File(["a"], "a.mp4", { type: "video/mp4" });
    const fileB = new File(["b"], "b.mp4", { type: "video/mp4" });

    await uploadVideos("token", [fileA, fileB]);

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/videos");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const formData = init.body as FormData;
    expect(formData.getAll("files")).toEqual([fileA, fileB]);
  });

  it("uploadVideos never sets a manual Content-Type — the browser must set the multipart boundary itself", async () => {
    const fetchMock = fetchMockReturning({ results: [] });
    vi.stubGlobal("fetch", fetchMock);

    await uploadVideos("token", [new File(["a"], "a.mp4")]);

    const [, init = {}] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("uploadVideos still attaches the Authorization header", async () => {
    const fetchMock = fetchMockReturning({ results: [] });
    vi.stubGlobal("fetch", fetchMock);

    await uploadVideos("real-token", [new File(["a"], "a.mp4")]);

    const [, init = {}] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer real-token");
  });
});
