"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Spinner } from "@/components/ui/Spinner";
import { useSession } from "@/lib/session";

/**
 * Client-side route gate for everything under /app/* (Milestone 3.6).
 *
 * This is a static export with no server-side session check possible —
 * the HTML shell itself is always publicly fetchable regardless of what
 * this gate does. Real data only ever comes from the backend API, which
 * independently verifies the bearer token on every request
 * (api/dependencies/auth.py) — that verification, not this gate, is the
 * actual security boundary. This component exists purely for UX (don't
 * show an authenticated-shaped shell to a signed-out visitor, and send
 * them to /login instead).
 */
export function AppAuthGate({ children }: { children: React.ReactNode }) {
  const { status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return (
      <div className="flex min-h-full items-center justify-center">
        <Spinner label="Loading your account…" />
      </div>
    );
  }
  if (status === "unauthenticated") {
    return null;
  }
  return <>{children}</>;
}
