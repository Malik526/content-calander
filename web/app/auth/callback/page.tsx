"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase/client";

/**
 * /auth/callback — where Google/Supabase redirects the browser back to
 * after sign-in (Milestone 3.6, Phase 9). supabase-js's client is
 * configured with detectSessionInUrl: true (lib/supabase/client.ts), so
 * it already exchanges the URL's auth code for a real session by the time
 * this page mounts — this page only needs to wait for that and then
 * navigate into the product, or show a clear error if it never resolves.
 */
export default function AuthCallbackPage() {
  const router = useRouter();
  // Resolved synchronously at render time, not inside the effect below —
  // isSupabaseConfigured is a build-time constant, so there's no reason
  // to defer this outcome past the first render (same reasoning as
  // lib/session.tsx's computeInitialSession).
  const [errorMessage, setErrorMessage] = useState<string | null>(() =>
    isSupabaseConfigured ? null : "Sign-in is not configured in this environment.",
  );

  useEffect(() => {
    if (!isSupabaseConfigured) return; // already resolved above
    const supabase = getSupabaseClient();
    let cancelled = false;

    const timeout = setTimeout(() => {
      if (!cancelled) setErrorMessage("Sign-in is taking longer than expected. Try signing in again.");
    }, 8000);

    const { data: listener } = supabase.auth.onAuthStateChange((event, session) => {
      if (session) {
        clearTimeout(timeout);
        router.replace("/app");
      }
    });

    supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        clearTimeout(timeout);
        router.replace("/app");
      }
    });

    return () => {
      cancelled = true;
      clearTimeout(timeout);
      listener.subscription.unsubscribe();
    };
  }, [router]);

  return (
    <main className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      {errorMessage ? (
        <ErrorState message={errorMessage} onRetry={() => router.replace("/login")} />
      ) : (
        <Spinner label="Finishing sign-in…" />
      )}
    </main>
  );
}
