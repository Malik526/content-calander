import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AppHomePage from "@/app/app/page";
import LibraryPage from "@/app/app/library/page";
import QueuePage from "@/app/app/queue/page";
import SettingsPage from "@/app/app/settings/page";
import { SessionProvider } from "@/lib/session";

/**
 * The four /app/* product routes (Milestone 3.5). Each renders its real,
 * shipped-empty-by-default state — no mock data is force-fed in here,
 * since that empty state IS the real behavior these pages ship with
 * (see app/app/library/page.tsx and app/app/queue/page.tsx comments).
 * SettingsPage is the only one that reads useSession(), so it alone
 * needs the SessionProvider wrapper AppLayout normally supplies.
 */

describe("/app product routes", () => {
  it("renders /app (Home) with links to the other three sections", () => {
    render(<AppHomePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /library/i })).toHaveAttribute("href", "/app/library");
    expect(screen.getByRole("link", { name: /queue/i })).toHaveAttribute("href", "/app/queue");
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute("href", "/app/settings");
  });

  it("renders /app/library in its real empty state", () => {
    // The dev-mock session (no Supabase configured in this test env) has
    // no real accessToken, so this never calls the real backend — it
    // shows the same "no videos yet" state a genuinely empty account
    // would, which is the honest outcome either way (see the page's own
    // comment). settings-tiktok.test.tsx/library.test.tsx cover the real,
    // backend-connected states with a mocked session + mocked API.
    render(
      <SessionProvider>
        <LibraryPage />
      </SessionProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Library" })).toBeInTheDocument();
    expect(screen.getByText(/no videos yet/i)).toBeInTheDocument();
  });

  it("renders /app/queue in its real empty state", () => {
    render(<QueuePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeInTheDocument();
    expect(screen.getByText(/nothing scheduled yet/i)).toBeInTheDocument();
  });

  it("renders /app/settings with the current session's account details", () => {
    render(
      <SessionProvider>
        <SettingsPage />
      </SessionProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Local Creator")).toBeInTheDocument();
    expect(screen.getByText("local@pickle-batch.local")).toBeInTheDocument();
  });
});
