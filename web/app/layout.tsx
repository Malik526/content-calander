import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import { siteConfig } from "@/lib/site-config";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: siteConfig.name,
    template: `%s — ${siteConfig.name}`,
  },
  description: siteConfig.description,
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

// Milestone 3.5 (Phase 21 — installable/PWA decision, recorded not
// implemented as a full PWA): a manifest + theme-color + a correct mobile
// viewport is the cheap, worthwhile subset — no service worker, no
// offline logic. See docs/decisions/0010-frontend-app-shell.md "PWA".
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#4338ca",
};

/**
 * Root layout — deliberately minimal (Milestone 3.5). Owns only what is
 * genuinely global: html lang/font/antialiasing and metadata defaults.
 * Chrome (header/footer/nav) is owned by the nested layouts of each
 * top-level section instead — `(marketing)` for the public site,
 * `app/` for the product shell — so the two can diverge completely
 * without either one fighting the root layout's assumptions. See
 * docs/decisions/0010-frontend-app-shell.md "Public/Product Route
 * Boundary".
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col font-sans">{children}</body>
    </html>
  );
}
