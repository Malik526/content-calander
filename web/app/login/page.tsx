"use client";

import { useState } from "react";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase/client";
import { ErrorState } from "@/components/ui/ErrorState";

/**
 * /login — the smallest real sign-in UI (Milestone 3.6, Phase 8). One
 * provider (Google, via Supabase Auth) is enough for V1 — no email/
 * password, no password reset, no multi-provider linking UI, per the
 * milestone's own explicit guardrail. Public route: no SessionProvider/
 * useSession() dependency, so it renders even for a fully signed-out
 * visitor (the whole point of this page).
 */
export default function LoginPage() {
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSignIn() {
    if (!isSupabaseConfigured) {
      setStatus("error");
      setErrorMessage("Sign-in is not configured in this environment.");
      return;
    }
    setStatus("loading");
    const supabase = getSupabaseClient();
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setStatus("error");
      setErrorMessage(error.message);
    }
    // On success, the browser navigates away to Google — no further local
    // state change happens here.
  }

  return (
    <main className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm text-center">
        <h1 className="text-xl font-semibold tracking-tight text-ink">Sign in to Pickle Batch</h1>
        <p className="mt-2 text-sm text-ink-muted">Use your Google account to continue.</p>

        <button
          type="button"
          onClick={handleSignIn}
          disabled={status === "loading"}
          className="mt-8 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-60"
        >
          {status === "loading" ? "Redirecting…" : "Continue with Google"}
        </button>

        {status === "error" && errorMessage ? (
          <div className="mt-6">
            <ErrorState message={errorMessage} onRetry={handleSignIn} />
          </div>
        ) : null}
      </div>
    </main>
  );
}
