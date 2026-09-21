"use client";

import Link from "next/link";
import { useSession } from "@/lib/session";

/** Product app header — distinct from the marketing site's SiteHeader
 * (components/layout/SiteHeader.tsx): shows the current session instead
 * of marketing nav/CTA. Sign-out (Milestone 3.6) calls the real session
 * boundary's signOut() — a no-op for the dev mock session, a real
 * Supabase sign-out otherwise (see lib/session.tsx). */
export function AppHeader() {
  const { user, signOut } = useSession();

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
      <div className="flex h-14 items-center justify-between px-4 md:px-6">
        <Link href="/app" className="text-sm font-semibold tracking-tight text-ink">
          Pickle Batch
        </Link>
        {user ? (
          <div className="flex items-center gap-3 text-sm text-ink-muted">
            <span className="hidden sm:inline">{user.displayName}</span>
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent"
            >
              {user.displayName.charAt(0)}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-md px-2 py-1 text-xs font-medium text-ink-muted hover:text-ink"
            >
              Sign out
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
