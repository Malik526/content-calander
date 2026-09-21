import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppBottomNav, AppSideNav } from "@/components/app/AppNav";

const EXPECTED_DESTINATIONS = [
  { label: "Home", href: "/app" },
  { label: "Library", href: "/app/library" },
  { label: "Queue", href: "/app/queue" },
  { label: "Settings", href: "/app/settings" },
];

let mockPathname = "/app";
vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

describe("AppBottomNav (mobile)", () => {
  it("renders exactly the four product destinations", () => {
    mockPathname = "/app";
    render(<AppBottomNav />);
    const nav = screen.getByRole("navigation", { name: /product navigation/i });
    const links = nav.querySelectorAll("a");
    expect(links).toHaveLength(EXPECTED_DESTINATIONS.length);
    for (const { label, href } of EXPECTED_DESTINATIONS) {
      expect(screen.getByRole("link", { name: new RegExp(label, "i") })).toHaveAttribute("href", href);
    }
  });

  it("marks only the current route as the active page", () => {
    mockPathname = "/app/library";
    render(<AppBottomNav />);
    expect(screen.getByRole("link", { name: /library/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^home/i })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: /queue/i })).not.toHaveAttribute("aria-current");
  });

  it("treats a nested route under a section as still active for that section", () => {
    mockPathname = "/app/queue/some-nested-detail";
    render(<AppBottomNav />);
    expect(screen.getByRole("link", { name: /queue/i })).toHaveAttribute("aria-current", "page");
  });

  it("does not mark Home active for a different top-level section (prefix-match guard)", () => {
    mockPathname = "/app/library";
    render(<AppBottomNav />);
    expect(screen.getByRole("link", { name: /^home/i })).not.toHaveAttribute("aria-current", "page");
  });
});

describe("AppSideNav (desktop)", () => {
  it("renders the same four destinations as the mobile nav", () => {
    mockPathname = "/app/settings";
    render(<AppSideNav />);
    for (const { label, href } of EXPECTED_DESTINATIONS) {
      expect(screen.getByRole("link", { name: new RegExp(label, "i") })).toHaveAttribute("href", href);
    }
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute("aria-current", "page");
  });
});
