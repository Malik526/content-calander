import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { brandTheme } from "@/lib/brand-theme";

/**
 * Mobile browser chrome (Milestone 3.14 final follow-up). theme-color drives
 * iOS Safari's status bar/toolbar tint and Android's address bar; it was
 * still the pre-rebrand indigo after the CSS moved to pickle green. These
 * checks keep every non-CSS copy of the brand colors in step with the
 * globals.css tokens and keep the old purple out of the active config.
 */

vi.mock("next/font/google", () => ({ Geist: () => ({ variable: "font-geist" }) }));

const webRoot = path.resolve(__dirname, "../..");
const read = (relative: string) => readFileSync(path.join(webRoot, relative), "utf8");
const OLD_INDIGO = /#4338ca/i;

function cssToken(name: string): string {
  const match = read("app/globals.css").match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`${name} not found in globals.css`);
  return match[1].toLowerCase();
}

describe("brand theme colors", () => {
  it("mirror the globals.css brand tokens", () => {
    expect(brandTheme.themeColor).toBe(cssToken("--color-accent"));
    expect(brandTheme.backgroundColor).toBe(cssToken("--color-background"));
  });

  it("drive the root layout's theme-color viewport metadata", async () => {
    const { viewport } = await import("@/app/layout");
    expect(viewport.themeColor).toBe(brandTheme.themeColor);
  });

  it("match the web manifest", () => {
    const manifest = JSON.parse(read("public/manifest.webmanifest"));
    expect(manifest.theme_color).toBe(brandTheme.themeColor);
    expect(manifest.background_color).toBe(brandTheme.backgroundColor);
  });

  it("leave no pre-rebrand indigo in the active mobile configuration", () => {
    for (const file of ["app/layout.tsx", "public/manifest.webmanifest", "app/globals.css", "lib/brand-theme.ts"]) {
      expect(read(file), file).not.toMatch(OLD_INDIGO);
    }
  });
});
