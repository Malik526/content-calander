import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CaptionEditor } from "@/components/app/CaptionEditor";
import { ApiError } from "@/lib/api/client";
import type { CaptionResponse } from "@/lib/api/types";

/**
 * CaptionEditor (Milestone 3.10: Caption Generation + Editing) — the
 * explicit-regeneration and generation-availability rules. Save/persist
 * through the Queue is covered by tests/routes/queue-board.test.tsx.
 */

function caption(overrides: Partial<CaptionResponse> = {}): CaptionResponse {
  return { video_id: 1, caption_text: null, provenance: "NONE", can_generate: true, editable: true, ...overrides };
}

describe("CaptionEditor", () => {
  it("generates directly when there is no caption yet", async () => {
    const onGenerate = vi.fn().mockResolvedValue(caption({ caption_text: "from transcript", provenance: "GENERATED" }));
    render(<CaptionEditor caption={caption()} onSave={vi.fn()} onGenerate={onGenerate} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("from transcript"));
    expect(onGenerate).toHaveBeenCalledWith(false);
  });

  it("requires explicit confirmation before regenerating over an existing caption", async () => {
    const onGenerate = vi.fn().mockResolvedValue(caption({ caption_text: "regenerated", provenance: "GENERATED" }));
    render(
      <CaptionEditor
        caption={caption({ caption_text: "my edited caption", provenance: "GENERATED_EDITED" })}
        onSave={vi.fn()}
        onGenerate={onGenerate}
      />,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onGenerate).not.toHaveBeenCalled();
    expect(screen.getByText(/Your text will be lost/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onGenerate).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Caption")).toHaveValue("my edited caption");

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await user.click(screen.getByRole("button", { name: "Replace caption" }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByLabelText("Caption")).toHaveValue("regenerated"));
  });

  it("treats unsaved draft text as something regeneration would replace", async () => {
    const onGenerate = vi.fn();
    render(<CaptionEditor caption={caption()} onSave={vi.fn()} onGenerate={onGenerate} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Caption"), "unsaved draft");
    await user.click(screen.getByRole("button", { name: "Regenerate" }));

    expect(onGenerate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Replace caption" })).toBeInTheDocument();
  });

  it("hides generation and explains why when no transcript is available", () => {
    render(<CaptionEditor caption={caption({ can_generate: false })} onSave={vi.fn()} onGenerate={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
    expect(screen.getByText(/need a transcript/)).toBeInTheDocument();
  });

  it("shows a backend error inline and keeps the draft", async () => {
    const onSave = vi.fn().mockRejectedValue(new ApiError("Caption is locked."));
    render(<CaptionEditor caption={caption({ can_generate: false })} onSave={onSave} onGenerate={vi.fn()} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Caption"), "hello");
    await user.click(screen.getByRole("button", { name: "Save caption" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Caption is locked."));
    expect(screen.getByLabelText("Caption")).toHaveValue("hello");
  });
});
