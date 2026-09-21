import { afterEach, describe, expect, it, vi } from "vitest";
import { connectTikTok, disconnectTikTok, getMe, getTikTokConnection } from "@/lib/api/platforms";

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function fetchMockReturning(body: unknown) {
  return vi.fn<typeof fetch>(async () => jsonResponse(body));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lib/api/platforms.ts", () => {
  it("getMe attaches the access token as a Bearer header", async () => {
    const fetchMock = fetchMockReturning({ id: 1, email: "a@example.com", display_name: "A" });
    vi.stubGlobal("fetch", fetchMock);

    await getMe("real-token");

    const [, init = {}] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer real-token");
  });

  it("getMe sends no Authorization header when accessToken is null", async () => {
    const fetchMock = fetchMockReturning({ id: 1, email: "a@example.com", display_name: "A" });
    vi.stubGlobal("fetch", fetchMock);

    await getMe(null);

    const [, init = {}] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("getTikTokConnection calls the status endpoint", async () => {
    const fetchMock = fetchMockReturning({ platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getTikTokConnection("token");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/platforms/tiktok/status");
    expect(result.connected).toBe(false);
  });

  it("connectTikTok POSTs to the connect endpoint and returns the authorization_url", async () => {
    const fetchMock = fetchMockReturning({ authorization_url: "https://www.tiktok.com/v2/auth/authorize/?x=1" });
    vi.stubGlobal("fetch", fetchMock);

    const result = await connectTikTok("token");

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/platforms/tiktok/connect");
    expect(init.method).toBe("POST");
    expect(result.authorization_url).toContain("tiktok.com");
  });

  it("disconnectTikTok POSTs to the disconnect endpoint", async () => {
    const fetchMock = fetchMockReturning({ platform: "tiktok", connected: false, status: "DISCONNECTED", account_label: null });
    vi.stubGlobal("fetch", fetchMock);

    await disconnectTikTok("token");

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/platforms/tiktok/disconnect");
    expect(init.method).toBe("POST");
  });
});
