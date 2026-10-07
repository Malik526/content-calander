import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AppHomePage from "@/app/app/page";
import LibraryPage from "@/app/app/library/page";
import QueuePage from "@/app/app/queue/page";
import SettingsPage from "@/app/app/settings/page";
import { SessionProvider } from "@/lib/session";
import { AppDataProviders } from "@/components/app/AppDataProviders";

/**
 * The four /app/* product routes (Milestone 3.5). Each renders its real,
 * shipped-empty-by-default state — no mock data is force-fed in here,
 * since that empty state IS the real behavior these pages ship with
 * (see app/app/library/page.tsx and app/app/queue/page.tsx comments).
 * SettingsPage only reads useSession(); LibraryPage reads both
 * useSession() and useUploadManager() (Milestone 3.7 follow-up), so it
 * alone needs both wrappers AppLayout normally supplies.
 */

describe("/app product routes", () => {
  // Milestone 3.15: every page reads server state through the shared cache,
  // so each renders inside SessionProvider + AppDataProviders like the real
  // layout. With no Supabase configured here the dev-mock session has no
  // accessToken, so the hooks resolve their empty defaults without calling
  // any backend — asynchronously, hence findBy*.
  function renderInApp(page: React.ReactElement) {
    return render(
      <SessionProvider>
        <AppDataProviders>{page}</AppDataProviders>
      </SessionProvider>,
    );
  }

  it("renders /app (Home) with a setup checklist linking to the other sections", async () => {
    renderInApp(<AppHomePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /upload in library/i })).toHaveAttribute("href", "/app/library");
    expect(screen.getByRole("link", { name: /set up in queue/i })).toHaveAttribute("href", "/app/queue");
    expect(screen.getByRole("link", { name: /connect in settings/i })).toHaveAttribute("href", "/app/settings");
  });

  it("renders /app/library in its real empty state", async () => {
    renderInApp(<LibraryPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Library" })).toBeInTheDocument();
    expect(await screen.findByText(/no videos yet/i)).toBeInTheDocument();
  });

  it("renders /app/queue in its real empty state", async () => {
    renderInApp(<QueuePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeInTheDocument();
    expect(await screen.findByText("No unscheduled videos — upload one in Library.")).toBeInTheDocument();
    expect(screen.getByText("No slots in this window yet.")).toBeInTheDocument();
  });

  it("renders /app/settings with the current session's account details", async () => {
    renderInApp(<SettingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Local Creator")).toBeInTheDocument();
    expect(screen.getByText("local@pickle-batch.local")).toBeInTheDocument();
    // Milestone 4.0: TikTok and Instagram each show their own (dev-mock) status.
    // Milestone 4.1: the Instagram card is its own component and may settle
    // a tick after TikTok's, so wait for both rather than the first match.
    await waitFor(() => expect(screen.getAllByText("Not connected")).toHaveLength(2));
  });
});
