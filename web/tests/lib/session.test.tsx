import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionProvider, useSession } from "@/lib/session";

/**
 * Milestone 3.6: lib/session.tsx now wraps real Supabase Auth instead of
 * an always-on mock. These tests mock lib/supabase/client.ts's exported
 * surface (isSupabaseConfigured / getSupabaseClient) rather than hitting
 * a real Supabase project — the fail-closed invariant this file locks in
 * (a production build with Supabase unconfigured must fail closed, with
 * NO bypass flag — the Milestone 3.5 NEXT_PUBLIC_ALLOW_MOCK_SESSION flag
 * was removed entirely this milestone) is what actually matters here, not
 * live Supabase behavior.
 */

const mockState = vi.hoisted(() => ({ configured: false }));

vi.mock("@/lib/supabase/client", () => ({
  get isSupabaseConfigured() {
    return mockState.configured;
  },
  getSupabaseClient: vi.fn(),
}));

import { getSupabaseClient } from "@/lib/supabase/client";

function Probe() {
  const { status, user, accessToken } = useSession();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="email">{user?.email ?? ""}</span>
      <span data-testid="token">{accessToken ?? ""}</span>
    </div>
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
  mockState.configured = false;
  vi.clearAllMocks();
});

describe("SessionProvider — dev fallback (Supabase not configured)", () => {
  it("renders the dev mock user outside of a production build", () => {
    vi.stubEnv("NODE_ENV", "test");
    mockState.configured = false;

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );

    expect(screen.getByTestId("status")).toHaveTextContent("authenticated");
    expect(screen.getByTestId("email")).toHaveTextContent("local@pickle-batch.local");
    // The dev mock never carries a real backend-verifiable token.
    expect(screen.getByTestId("token")).toHaveTextContent("");
  });

  it("fails closed in a production build with no bypass flag of any kind", () => {
    vi.stubEnv("NODE_ENV", "production");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() =>
      render(
        <SessionProvider>
          <Probe />
        </SessionProvider>,
      ),
    ).toThrow(/Supabase is not configured/i);

    consoleError.mockRestore();
  });
});

describe("SessionProvider — Supabase configured", () => {
  function mockSupabaseSession(session: { user: { id: string; email: string; user_metadata?: object } } | null) {
    const onAuthStateChange = vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    const getSession = vi.fn().mockResolvedValue({
      data: { session: session ? { ...session, access_token: "real-access-token" } : null },
    });
    const signOut = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(getSupabaseClient).mockReturnValue({
      auth: { getSession, onAuthStateChange, signOut },
    } as unknown as ReturnType<typeof getSupabaseClient>);
    return { signOut };
  }

  it("renders an authenticated session derived from a real Supabase session", async () => {
    vi.stubEnv("NODE_ENV", "test");
    mockState.configured = true;
    mockSupabaseSession({ user: { id: "u1", email: "creator@example.com", user_metadata: { name: "Creator One" } } });

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    expect(screen.getByTestId("email")).toHaveTextContent("creator@example.com");
    expect(screen.getByTestId("token")).toHaveTextContent("real-access-token");
  });

  it("renders unauthenticated when Supabase has no session", async () => {
    vi.stubEnv("NODE_ENV", "test");
    mockState.configured = true;
    mockSupabaseSession(null);

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("unauthenticated"));
    expect(screen.getByTestId("email")).toHaveTextContent("");
  });
});
