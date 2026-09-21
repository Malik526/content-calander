"use client";

/**
 * session.tsx — the one auth/session boundary the app shell depends on
 * (Milestone 3.5, Phase 14/15).
 *
 * No real authentication provider is selected or integrated here — see
 * docs/decisions/0010-frontend-app-shell.md "Authentication Boundary".
 * `useSession()` is the only thing product pages/components should ever
 * import to find out "who is the current user" — never a hardcoded mock
 * user imported directly, and never a real provider's SDK. Swapping in
 * real authentication later means rewriting SessionProvider's internals
 * only; every consumer of `useSession()` is unaffected.
 *
 * DEV_MOCK_USER is explicitly, unmissably a development-only stand-in —
 * see the guard below, which throws in a production build if this
 * provider is ever reached without a real session mechanism replacing
 * it. This is intentional friction: shipping the mock silently to
 * production would be worse than a loud build-time reminder.
 *
 * BLOCKER BEFORE REAL DATA ACCESS: the deployed Netlify build currently
 * sets NEXT_PUBLIC_ALLOW_MOCK_SESSION=true (see ../netlify.toml) purely
 * because Milestone 3.5 is a static shell with no real backend and no
 * user-owned data to protect. That flag is a temporary shell-development
 * setting, not a shipped product decision, and it is not itself a
 * secret — it is NEXT_PUBLIC_ and visible in the browser bundle by
 * design. The invariant that must hold before any product route is
 * allowed to read/write real user-owned data or call an authenticated
 * API: replace this module's mock session with a real,
 * server-verifiable authentication mechanism, then delete
 * NEXT_PUBLIC_ALLOW_MOCK_SESSION from netlify.toml so the production
 * guard below goes back to failing closed by default. Do not widen this
 * flag's scope, do not read it anywhere outside this file, and never
 * pair it with a real user id, service-role key, database credential,
 * or privileged token in client-bundled code (see
 * docs/decisions/0010-frontend-app-shell.md "Authentication Boundary").
 */

import { createContext, useContext, type ReactNode } from "react";

export interface SessionUser {
  id: string;
  email: string;
  displayName: string;
}

export type SessionStatus = "authenticated" | "unauthenticated" | "loading";

export interface Session {
  status: SessionStatus;
  user: SessionUser | null;
}

const SessionContext = createContext<Session | null>(null);

// Mirrors persistence's own local-bootstrap-user pattern
// (ContentStore.get_or_create_local_user, docs/decisions/0007-user-ownership-model.md)
// purely as a naming convention, not a shared identity — this is
// frontend-only, dev-only, and never reaches any real backend call.
const DEV_MOCK_USER: SessionUser = {
  id: "dev-local-user",
  email: "local@pickle-batch.local",
  displayName: "Local Creator",
};

export function SessionProvider({ children }: { children: ReactNode }) {
  if (process.env.NODE_ENV === "production" && process.env.NEXT_PUBLIC_ALLOW_MOCK_SESSION !== "true") {
    throw new Error(
      "SessionProvider is using DEV_MOCK_USER, a development-only stand-in for real authentication " +
        "(see lib/session.tsx). Replace it with a real session mechanism before shipping this build " +
        "to production, or set NEXT_PUBLIC_ALLOW_MOCK_SESSION=true only for an intentional preview " +
        "deployment that is not the real product.",
    );
  }

  const session: Session = { status: "authenticated", user: DEV_MOCK_USER };
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error("useSession() was called outside a <SessionProvider>.");
  }
  return session;
}
