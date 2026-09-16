import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Milestone 2.0.1: this app has no server-rendered/dynamic routes yet
  // (no auth, no data fetching, no forms) — a static export is the
  // simplest, most Netlify-ready deployment for exactly what exists today.
  // Remove this once /app/* needs real server rendering.
  output: "export",
  // /privacy -> privacy/index.html (not privacy.html) — the most portable
  // static-hosting shape; no host-specific extension-guessing required.
  trailingSlash: true,
};

export default nextConfig;
