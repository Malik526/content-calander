"use client";

import Link from "next/link";
import { useSession } from "@/lib/session";

/** Product app header — distinct from the marketing site's SiteHeader
 * (components/layout/SiteHeader.tsx): shows the current session instead
 * of marketing nav/CTA. No logout wiring yet (Phase 14 — session
 * boundary exists, real auth does not), but the affordance is real so
 * adding it later is a small change, not a new layout. */
export function AppHeader() {
  const { user } = useSession();

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
      <div className="flex h-14 items-center justify-between px-4 md:px-6">
        <Link href="/app" className="text-sm font-semibold tracking-tight text-ink">
          Pickle Batch
        </Link>
        {user ? (
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <span className="hidden sm:inline">{user.displayName}</span>
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent"
            >
              {user.displayName.charAt(0)}
            </span>
          </div>
        ) : null}
      </div>
    </header>
  );
}
