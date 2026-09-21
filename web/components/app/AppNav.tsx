"use client";

/**
 * AppNav.tsx — product navigation (Milestone 3.5, Phase 10).
 *
 * One component, not two: desktop and mobile share the same nav item
 * list and active-route logic, diverging only via Tailwind breakpoint
 * classes — the same pattern components/layout/SiteHeader.tsx already
 * established for the marketing site's own mobile menu. Mobile renders a
 * fixed bottom tab bar (reachable one-handed); desktop renders a left
 * sidebar. Four destinations only — Home, Library, Queue, Settings —
 * matching Milestone 3.5's own near-term roadmap; Analytics/Pickle
 * Agent/etc. are deliberately not here yet (see
 * docs/decisions/0010-frontend-app-shell.md "Product Navigation").
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/app", label: "Home", icon: HomeIcon },
  { href: "/app/library", label: "Library", icon: LibraryIcon },
  { href: "/app/queue", label: "Queue", icon: QueueIcon },
  { href: "/app/settings", label: "Settings", icon: SettingsIcon },
] as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/app") return pathname === "/app" || pathname === "/app/";
  return pathname.startsWith(href);
}

export function AppBottomNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Product navigation"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/95 backdrop-blur md:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <ul className="grid grid-cols-4">
        {navItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex min-h-14 flex-col items-center justify-center gap-1 py-2 text-[11px] font-medium ${
                  active ? "text-accent" : "text-ink-muted"
                }`}
              >
                <item.icon active={active} />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function AppSideNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Product navigation" className="hidden w-56 shrink-0 md:block">
      <ul className="flex flex-col gap-1">
        {navItems.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${
                  active ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-background hover:text-ink"
                }`}
              >
                <item.icon active={active} />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

type IconProps = { active: boolean };

function HomeIcon({ active }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.2 : 1.8} className="h-5 w-5" aria-hidden="true">
      <path d="M4 11.5 12 4l8 7.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6 10v9a1 1 0 0 0 1 1h3v-5h4v5h3a1 1 0 0 0 1-1v-9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function LibraryIcon({ active }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.2 : 1.8} className="h-5 w-5" aria-hidden="true">
      <rect x="4" y="4" width="7" height="7" rx="1.2" />
      <rect x="13" y="4" width="7" height="7" rx="1.2" />
      <rect x="4" y="13" width="7" height="7" rx="1.2" />
      <rect x="13" y="13" width="7" height="7" rx="1.2" />
    </svg>
  );
}

function QueueIcon({ active }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.2 : 1.8} className="h-5 w-5" aria-hidden="true">
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <path d="M3.5 9.5h17M8 3v3M16 3v3" strokeLinecap="round" />
    </svg>
  );
}

function SettingsIcon({ active }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={active ? 2.2 : 1.8} className="h-5 w-5" aria-hidden="true">
      <circle cx="12" cy="12" r="3.2" />
      <path
        d="M19.4 13.5c.1-.5.1-1 0-1.5l1.7-1.3-1.5-2.6-2 .6a6.9 6.9 0 0 0-1.3-.75L16 6h-3l-.3 2a6.9 6.9 0 0 0-1.3.75l-2-.6L8 10.7l1.7 1.3c-.1.5-.1 1 0 1.5L8 14.8l1.5 2.6 2-.6c.4.3.85.55 1.3.75l.3 2h3l.3-2c.45-.2.9-.45 1.3-.75l2 .6 1.5-2.6-1.7-1.3Z"
        strokeLinejoin="round"
      />
    </svg>
  );
}
