"use client";

/**
 * session.tsx — the one auth/session boundary the app shell depends on.
 *
 * Milestone 3.5 established this boundary with a loudly-marked mock user.
 * Milestone 3.6 replaces the mock with real Supabase Auth (Google as the
 * first provider) — see
 * docs/decisions/0011-real-authentication-and-tiktok-connection.md
 * "Authentication". No consumer of useSession() needed to change for this
 * milestone; only this module's internals did.
 *
 * Dev-only fallback: if NEXT_PUBLIC_SUPABASE_URL/NEXT_PUBLIC_SUPABASE_ANON_KEY
 * are not set (e.g. a fresh local clone before Supabase is configured) AND
 * this is NOT a production build, SessionProvider falls back to a loudly-
 * marked mock user — the same shell-development convenience Milestone 3.5
 * offered, just no longer reachable in production at all: there is no
 * bypass flag anymore (NEXT_PUBLIC_ALLOW_MOCK_SESSION was removed from
 * this codebase and from netlify.toml this milestone). A production build
 * with Supabase unconfigured fails closed, unconditionally.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Session as SupabaseSession } from "@supabase/supabase-js";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase/client";

export interface SessionUser {
  id: string;
  email: string;
  displayName: string;
}

export type SessionStatus = "authenticated" | "unauthenticated" | "loading";

export interface Session {
  status: SessionStatus;
  user: SessionUser | null;
  /** The current Supabase access token, for lib/api/client.ts callers to
   * attach as `Authorization: Bearer <accessToken>`. Null when
   * unauthenticated, loading, or running the dev mock fallback (which has
   * no backend-verifiable token at all — see lib/api/client.ts's own
   * handling of a missing token). */
  accessToken: string | null;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

// Mirrors persistence's own local-bootstrap-user pattern
// (ContentStore.get_or_create_local_user,
// docs/decisions/0007-user-ownership-model.md) purely as a naming
// convention, not a shared identity — this is frontend-only, dev-only,
// and never reaches any real backend call (accessToken is null, so
// lib/api/client.ts never attaches a fake Authorization header for it).
const DEV_MOCK_USER: SessionUser = {
  id: "dev-local-user",
  email: "local@pickle-batch.local",
  displayName: "Local Creator",
};

const LOADING_SESSION: Session = { status: "loading", user: null, accessToken: null, signOut: async () => {} };
const DEV_MOCK_SESSION: Session = {
  status: "authenticated", user: DEV_MOCK_USER, accessToken: null, signOut: async () => {},
};

function toSessionUser(supabaseSession: SupabaseSession): SessionUser {
  const user = supabaseSession.user;
  const metadata = (user.user_metadata ?? {}) as Record<string, unknown>;
  const name =
    (metadata.name as string | undefined) ??
    (metadata.full_name as string | undefined) ??
    user.email?.split("@")[0] ??
    "Creator";
  return { id: user.id, email: user.email ?? "", displayName: name };
}

/** Resolved once, synchronously, at render time (a useState lazy
 * initializer — never inside an effect): the "Supabase isn't configured"
 * outcome is fully determined by build-time env vars, so there's no
 * async work to wait for and no reason to defer it past the first
 * render. Throwing here (production, unconfigured) fails the render
 * immediately, which is both simpler and more idiomatic than deferring
 * the same throw into a post-mount effect. */
function computeInitialSession(): Session {
  if (!isSupabaseConfigured) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "Supabase is not configured (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY) and this " +
          "is a production build — there is no mock-session fallback in production. Set both in the " +
          "deployment environment (see .env.example) before deploying. See " +
          "docs/decisions/0011-real-authentication-and-tiktok-connection.md \"Authentication\".",
      );
    }
    return DEV_MOCK_SESSION;
  }
  return LOADING_SESSION;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>(computeInitialSession);

  useEffect(() => {
    if (!isSupabaseConfigured) return; // already resolved by computeInitialSession above

    const supabase = getSupabaseClient();
    const signOut = async () => {
      await supabase.auth.signOut();
    };

    let cancelled = false;
    supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setSession(
        data.session
          ? { status: "authenticated", user: toSessionUser(data.session), accessToken: data.session.access_token, signOut }
          : { status: "unauthenticated", user: null, accessToken: null, signOut },
      );
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(
        newSession
          ? { status: "authenticated", user: toSessionUser(newSession), accessToken: newSession.access_token, signOut }
          : { status: "unauthenticated", user: null, accessToken: null, signOut },
      );
    });

    return () => {
      cancelled = true;
      listener.subscription.unsubscribe();
    };
  }, []);

  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error("useSession() was called outside a <SessionProvider>.");
  }
  return session;
}
