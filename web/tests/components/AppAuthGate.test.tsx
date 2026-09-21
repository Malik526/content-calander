import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppAuthGate } from "@/components/app/AppAuthGate";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

const mockSession = vi.hoisted(() => ({ status: "loading" as "loading" | "authenticated" | "unauthenticated" }));
vi.mock("@/lib/session", () => ({
  useSession: () => mockSession,
}));

describe("AppAuthGate", () => {
  it("shows a loading indicator while the session is resolving", () => {
    mockSession.status = "loading";
    render(
      <AppAuthGate>
        <div>Protected content</div>
      </AppAuthGate>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders the protected content once authenticated", () => {
    mockSession.status = "authenticated";
    render(
      <AppAuthGate>
        <div>Protected content</div>
      </AppAuthGate>,
    );
    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });

  it("redirects to /login and renders nothing when unauthenticated", () => {
    mockSession.status = "unauthenticated";
    replace.mockClear();
    render(
      <AppAuthGate>
        <div>Protected content</div>
      </AppAuthGate>,
    );
    expect(replace).toHaveBeenCalledWith("/login");
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });
});
