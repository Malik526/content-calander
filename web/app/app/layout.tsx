import { AppAuthGate } from "@/components/app/AppAuthGate";
import { AppHeader } from "@/components/app/AppHeader";
import { AppBottomNav, AppSideNav } from "@/components/app/AppNav";
import { SessionProvider } from "@/lib/session";
import { UploadProvider } from "@/lib/uploads";

/**
 * Product app shell layout — everything under `/app/*` (Milestone 3.5).
 * Structurally separate from the marketing site's chrome
 * (app/(marketing)/layout.tsx) — no SiteHeader/SiteFooter here at all.
 * Mobile-first: AppBottomNav is the primary navigation (one-handed
 * reachable); AppSideNav is the desktop enhancement of the exact same
 * nav model, not a separate app. Content gets bottom padding on mobile
 * so the fixed bottom nav never overlaps the last visible content.
 *
 * Milestone 3.6: AppAuthGate redirects a signed-out visitor to /login —
 * see that component's own docstring for why this is a UX affordance,
 * not the actual security boundary (the backend API is).
 *
 * Milestone 3.7 follow-up: UploadProvider lives here too, above
 * `{children}` — the same "persists across every /app/* navigation"
 * property SessionProvider already relies on — so an in-progress batch
 * upload's state survives the user navigating to Queue/Settings and back,
 * instead of being owned only by the Library page component. See
 * lib/uploads.tsx.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <UploadProvider>
        <AppAuthGate>
          <div className="flex min-h-full flex-col">
            <AppHeader />
            <div className="mx-auto flex w-full max-w-5xl flex-1 gap-8 px-4 pb-20 pt-6 md:px-6 md:pb-10">
              <AppSideNav />
              <main className="min-w-0 flex-1">{children}</main>
            </div>
            <AppBottomNav />
          </div>
        </AppAuthGate>
      </UploadProvider>
    </SessionProvider>
  );
}
