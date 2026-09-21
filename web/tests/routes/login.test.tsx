import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/login/page";

const mockState = vi.hoisted(() => ({ configured: true }));
const signInWithOAuth = vi.hoisted(() => vi.fn());

vi.mock("@/lib/supabase/client", () => ({
  get isSupabaseConfigured() {
    return mockState.configured;
  },
  getSupabaseClient: () => ({ auth: { signInWithOAuth } }),
}));

afterEach(() => {
  vi.clearAllMocks();
  mockState.configured = true;
});

describe("LoginPage", () => {
  it("renders a single Google sign-in action", () => {
    render(<LoginPage />);
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument();
  });

  it("starts the Supabase OAuth flow when clicked", async () => {
    signInWithOAuth.mockResolvedValue({ error: null });
    const user = userEvent.setup();

    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: /continue with google/i }));

    expect(signInWithOAuth).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "google" }),
    );
  });

  it("shows a retryable error state when sign-in fails", async () => {
    signInWithOAuth.mockResolvedValue({ error: { message: "OAuth provider error." } });
    const user = userEvent.setup();

    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: /continue with google/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("OAuth provider error."));
  });

  it("shows an error immediately when Supabase is not configured", async () => {
    mockState.configured = false;
    const user = userEvent.setup();

    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: /continue with google/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(signInWithOAuth).not.toHaveBeenCalled();
  });
});
