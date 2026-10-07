import { afterEach, describe, expect, it, vi } from "vitest";
import {
  connectInstagram,
  connectTikTok,
  disconnectInstagram,
  disconnectTikTok,
  getInstagramConnection,
  getMe,
  getTikTokConnection,
} from "@/lib/api/platforms";

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

  it("getInstagramConnection calls the Instagram status endpoint with the bearer token (Milestone 4.0)", async () => {
    const fetchMock = fetchMockReturning({
      platform: "instagram", connected: false, status: "DISCONNECTED", account_label: null, connect_available: false,
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getInstagramConnection("token");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/platforms/instagram/status");
    const [, init = {}] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(result.connect_available).toBe(false);
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

  // Milestone 4.1
  it("connectInstagram POSTs with the bearer token and no body by default", async () => {
    const fetchMock = fetchMockReturning({ authorization_url: "https://www.instagram.com/oauth/authorize?x=1" });
    vi.stubGlobal("fetch", fetchMock);

    const result = await connectInstagram("token");

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/platforms/instagram/connect");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(result.authorization_url).toContain("instagram.com");
  });

  it("connectInstagram sends a requested return target as JSON", async () => {
    const fetchMock = fetchMockReturning({ authorization_url: "https://www.instagram.com/oauth/authorize?x=1" });
    vi.stubGlobal("fetch", fetchMock);

    await connectInstagram("token", "https://app.example.com/app/settings");

    const [, init = {}] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ return_target: "https://app.example.com/app/settings" });
  });

  it("disconnectInstagram POSTs to the Instagram disconnect endpoint", async () => {
    const fetchMock = fetchMockReturning({
      platform: "instagram", connected: false, status: "DISCONNECTED", account_label: null, connect_available: true,
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await disconnectInstagram("token");

    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/platforms/instagram/disconnect");
    expect(init.method).toBe("POST");
    expect(result.connected).toBe(false);
  });
});
