import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";

describe("Button", () => {
  it("renders an internal href as a Next Link", () => {
    render(<Button href="/app/library">Go to Library</Button>);
    expect(screen.getByRole("link", { name: "Go to Library" })).toHaveAttribute("href", "/app/library");
  });

  it("renders an external href with target=_blank and rel=noreferrer", () => {
    render(
      <Button href="https://example.com" external>
        Visit
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Visit" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("renders a mailto: href as a plain anchor, never external-tagged", () => {
    render(<Button href="mailto:support@example.com">Email us</Button>);
    const link = screen.getByRole("link", { name: "Email us" });
    expect(link).toHaveAttribute("href", "mailto:support@example.com");
    expect(link).not.toHaveAttribute("target");
  });

  it("renders a real onClick action as a real <button>, never a disguised link", () => {
    const onClick = vi.fn();
    render(
      <Button type="button" onClick={onClick}>
        Connect
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Connect" });
    expect(button.tagName).toBe("BUTTON");
    button.click();
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders an action button as disabled when disabled is passed", () => {
    render(
      <Button type="button" onClick={() => {}} disabled>
        Connecting…
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Connecting…" })).toBeDisabled();
  });
});

describe("Card", () => {
  it("renders children and accepts an additional className", () => {
    render(<Card className="extra-class">content</Card>);
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByText("content")).toHaveClass("extra-class");
  });
});

describe("Badge", () => {
  it.each([
    ["pending", "Scheduled"],
    ["progress", "Publishing"],
    ["success", "Published"],
    ["danger", "Failed"],
  ] as const)("renders the %s tone", (tone, label) => {
    render(<Badge tone={tone}>{label}</Badge>);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("PageHeader", () => {
  it("renders a title, optional description, and optional action", () => {
    render(<PageHeader title="Queue" description="What's scheduled." action={<button>Do thing</button>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeInTheDocument();
    expect(screen.getByText("What's scheduled.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Do thing" })).toBeInTheDocument();
  });

  it("omits the description paragraph when none is given", () => {
    render(<PageHeader title="Queue" />);
    expect(screen.queryByText(/what's scheduled/i)).not.toBeInTheDocument();
  });
});

describe("Spinner (loading-state pattern)", () => {
  it("exposes role=status with an accessible label, not a silent animation", () => {
    render(<Spinner />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });

  it("accepts a custom label", () => {
    render(<Spinner label="Loading videos" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading videos");
  });
});

describe("EmptyState", () => {
  it("renders title, description, and an optional action", () => {
    render(<EmptyState title="No videos yet" description="Upload to get started." action={<button>Upload</button>} />);
    expect(screen.getByText("No videos yet")).toBeInTheDocument();
    expect(screen.getByText("Upload to get started.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });
});

describe("ErrorState (loading/error UX pattern)", () => {
  it("exposes role=alert with the normalized message", () => {
    render(<ErrorState message="Could not reach the server." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server.");
  });

  it("renders a retry action and invokes onRetry when clicked", async () => {
    const onRetry = vi.fn();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<ErrorState message="Failed to load." onRetry={onRetry} />);
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders no retry button when onRetry is omitted", () => {
    render(<ErrorState message="Failed to load." />);
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });
});
