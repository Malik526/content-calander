/**
 * brand-theme.ts — the Pickle Batch colors that live outside CSS: the
 * browser/OS chrome (theme-color — iOS Safari's status bar and toolbar,
 * Android's address bar) and the web manifest. They must mirror the
 * globals.css tokens named below; tests/lib/brand-theme.test.ts fails if
 * they drift, or if public/manifest.webmanifest disagrees.
 *
 * Milestone 3.14 final follow-up: replaces the pre-rebrand indigo
 * accent that was still hardcoded in layout.tsx and the manifest.
 */
export const brandTheme = {
  /** Browser chrome — mirrors --color-accent (pickle green). */
  themeColor: "#2c7a51",
  /** Manifest splash background — mirrors --color-background. */
  backgroundColor: "#f4f9f4",
} as const;
