"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type { CaptionProvenance, CaptionResponse } from "@/lib/api/types";

const PROVENANCE_LABEL: Record<CaptionProvenance, string> = {
  NONE: "No caption yet",
  MANUAL: "Written by you",
  GENERATED: "Generated",
  GENERATED_EDITED: "Generated, edited by you",
};

/**
 * CaptionEditor — view, edit, save and (re)generate one video's publishing
 * caption (Milestone 3.10: Caption Generation + Editing). Rendered inside
 * QueueSlotCard for any slot with an assigned video.
 *
 * Props:
 *   caption — the saved caption state (GET /api/videos/{id}/caption shape,
 *     carried on the Queue slot's assigned_video).
 *   onSave(text) — persist the draft; resolves with the saved state.
 *   onGenerate(overwrite) — generate and persist; resolves with the saved state.
 *
 * Regeneration never silently replaces text: if there is any saved caption
 * or unsaved draft, the user must confirm "Replace caption" first, and only
 * then is overwrite=true sent (the backend refuses otherwise). Once the
 * video has been submitted for publishing (caption.editable false), the
 * caption is shown read-only.
 */
export function CaptionEditor({
  caption,
  onSave,
  onGenerate,
}: {
  caption: CaptionResponse;
  onSave: (text: string) => Promise<CaptionResponse>;
  onGenerate: (overwrite: boolean) => Promise<CaptionResponse>;
}) {
  // --- State ---
  const textareaId = useId();
  const [draft, setDraft] = useState(caption.caption_text ?? "");
  const [busy, setBusy] = useState<"save" | "generate" | null>(null);
  const [confirmingReplace, setConfirmingReplace] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const saved = caption.caption_text ?? "";
  const dirty = draft !== saved;
  const hasText = saved.trim() !== "" || draft.trim() !== "";

  // --- Handlers ---
  async function run(kind: "save" | "generate", action: () => Promise<CaptionResponse>) {
    setError(null);
    setBusy(kind);
    try {
      const result = await action();
      setDraft(result.caption_text ?? "");
      setJustSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the caption.");
    } finally {
      setBusy(null);
      setConfirmingReplace(false);
    }
  }

  function handleGenerateClick() {
    if (hasText) {
      setConfirmingReplace(true);
      return;
    }
    void run("generate", () => onGenerate(false));
  }

  // --- Render ---
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={textareaId} className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Caption
        </label>
        <span className="text-xs text-ink-muted">{PROVENANCE_LABEL[caption.provenance]}</span>
      </div>

      <textarea
        id={textareaId}
        rows={3}
        value={draft}
        readOnly={!caption.editable}
        disabled={busy !== null}
        onChange={(event) => {
          setDraft(event.target.value);
          setJustSaved(false);
        }}
        placeholder={caption.editable ? "Write the caption this video will post with…" : ""}
        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink disabled:opacity-60"
      />

      {!caption.editable ? (
        <p className="text-xs text-ink-muted">This video has been sent for publishing, so its caption can no longer change.</p>
      ) : confirmingReplace ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-ink">Replace the current caption with a generated one? Your text will be lost.</p>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run("generate", () => onGenerate(true))}
            className="rounded-lg border border-status-danger px-3 py-1.5 text-xs font-medium text-status-danger"
          >
            Replace caption
          </button>
          <button
            type="button"
            onClick={() => setConfirmingReplace(false)}
            className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-muted"
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {caption.can_generate ? (
            <button
              type="button"
              disabled={busy !== null}
              onClick={handleGenerateClick}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:border-accent/40 hover:text-accent disabled:opacity-60"
            >
              {busy === "generate" ? "Generating…" : hasText ? "Regenerate" : "Generate"}
            </button>
          ) : null}
          <button
            type="button"
            disabled={!dirty || busy !== null}
            onClick={() => void run("save", () => onSave(draft))}
            className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:border-accent/40 hover:text-accent disabled:opacity-60"
          >
            {busy === "save" ? "Saving…" : "Save caption"}
          </button>
          <span className="text-xs text-ink-muted">
            {justSaved && !dirty ? "Saved · " : ""}
            {draft.length} characters
          </span>
        </div>
      )}

      {caption.editable && !caption.can_generate ? (
        <p className="text-xs text-ink-muted">
          Automatic captions need a transcript, which isn&apos;t available for this video yet — write one above.
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="text-xs text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
