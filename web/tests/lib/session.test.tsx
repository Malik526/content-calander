import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionProvider, useSession } from "@/lib/session";

/**
 * Locks in the fail-closed invariant from lib/session.tsx: a production
 * build must refuse to render the dev-only mock session unless
 * NEXT_PUBLIC_ALLOW_MOCK_SESSION is explicitly set (as netlify.toml
 * currently does, deliberately, for the Milestone 3.5 shell only). See
 * "BLOCKER BEFORE REAL DATA ACCESS" in lib/session.tsx and netlify.toml.
 */

function Probe() {
  const { user } = useSession();
  return <span>{user?.email}</span>;
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("SessionProvider", () => {
  it("renders the dev mock user outside of a production build", () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("NEXT_PUBLIC_ALLOW_MOCK_SESSION", "");

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );

    expect(screen.getByText("local@pickle-batch.local")).toBeInTheDocument();
  });

  it("fails closed in a production build without the explicit opt-in flag", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_ALLOW_MOCK_SESSION", "");
    // React logs the thrown render error to console.error even when the
    // test expects and catches it — silence that expected noise only.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() =>
      render(
        <SessionProvider>
          <Probe />
        </SessionProvider>,
      ),
    ).toThrow(/development-only stand-in/i);

    consoleError.mockRestore();
  });

  it("renders the mock user in production only with the explicit opt-in flag set", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_ALLOW_MOCK_SESSION", "true");

    render(
      <SessionProvider>
        <Probe />
      </SessionProvider>,
    );

    expect(screen.getByText("local@pickle-batch.local")).toBeInTheDocument();
  });
});
