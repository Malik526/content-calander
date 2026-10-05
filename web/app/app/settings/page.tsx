"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { PlatformConnectionCard } from "@/components/app/PlatformConnectionCard";
import { useInstagramConnection } from "@/hooks/useInstagramConnection";
import { useTikTokActions } from "@/hooks/useTikTokActions";
import { useTikTokConnection } from "@/hooks/useTikTokConnection";
import { ApiError } from "@/lib/api/client";
import { useSession } from "@/lib/session";
import { connectedAccountName } from "@/lib/tiktokAccount";

/**
 * /app/settings (Milestone 3.6). Account section reflects the real
 * session boundary (lib/session.tsx — real Supabase Auth as of this
 * milestone). Connected-platforms section now calls the real backend
 * (lib/api/platforms.ts) instead of lib/api/mockData.ts — TikTok OAuth
 * itself is real end to end: "Connect" starts the hosted flow
 * (POST /api/platforms/tiktok/connect), the browser navigates to TikTok,
 * and TikTok's callback lands back here with a `?tiktok=` query param
 * this page reads to show a fresh status without a manual refresh.
 *
 * States shown: loading, disconnected, connecting (client-side, while the
 * connect request is in flight before the browser navigates away),
 * connected, error. There is no distinct "reauthorization required" UI
 * state yet — the backend's status response does not currently surface
 * that as a separate value from "disconnected" (see
 * docs/decisions/0011-real-authentication-and-tiktok-connection.md
 * "Deferred") — recorded as a gap, not fabricated.
 *
 * Milestone 3.15: connection status comes from the shared cache
 * (useTikTokConnection, also read by Home), so revisiting Settings shows
 * the last status at once. Disconnect writes its result into that cache.
 * A failed background refresh keeps the last status on screen with a
 * Retry notice; only a first-load failure replaces the card.
 *
 * Milestone 4.0: an Instagram card shows that platform's real connection
 * status (useInstagramConnection) through the shared
 * PlatformConnectionCard. It offers no Connect button until the backend
 * reports connect_available (the 4.1 connect flow).
 *
 * Milestone 3.8.1: the posting-cadence editor that briefly lived here
 * (Milestone 3.8's "Scheduling" section) moved to /app/queue
 * (components/app/QueueScheduling.tsx) so it sits with the upcoming-slots
 * preview it drives, and so there is exactly one editor for the cadence
 * config rather than two. This page never reads or writes cadence data.
 */
export default function SettingsPage() {
  const { user } = useSession();
  const connectionQuery = useTikTokConnection();
  const { startConnect, disconnect } = useTikTokActions();
  const connection = connectionQuery.data ?? null;
  const instagramQuery = useInstagramConnection();
  const instagram = instagramQuery.data ?? null;
  const [connecting, setConnecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    // window.location is only available after mount (this is a static
    // export with no server-side render of query-param-dependent state) —
    // an effect is the correct place for this, not a lazy useState
    // initializer, which would crash the Node-based static build.
    const params = new URLSearchParams(window.location.search);
    const reason = params.get("tiktok");
    if (!reason) return;
    window.history.replaceState({}, "", window.location.pathname);
    if (reason !== "connected") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActionError(
        {
          denied: "TikTok authorization was denied or cancelled.",
          invalid_state: "The sign-in attempt could not be verified. Try connecting again.",
          expired_state: "That connection attempt expired. Try connecting again.",
          exchange_failed: "TikTok could not be reached to finish connecting. Try again.",
        }[reason] ?? "Something went wrong connecting TikTok.",
      );
    }
  }, []);

  async function handleConnect() {
    setActionError(null);
    setConnecting(true);
    try {
      // Web adapter: open the platform's authorization page by navigating.
      // The OAuth return is a full page load, so the cache starts fresh.
      window.location.href = await startConnect();
    } catch (error) {
      setConnecting(false);
      setActionError(error instanceof ApiError ? error.message : "Could not start connecting TikTok.");
    }
  }

  async function handleDisconnect() {
    setActionError(null);
    try {
      await disconnect();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Could not disconnect TikTok.");
    }
  }

  // Milestone 3.14 follow-up: which TikTok account is connected (null → plain "Connected").
  const accountName = connection ? connectedAccountName(connection) : null;

  return (
    <>
      <PageHeader title="Settings" description="Your account and connected platforms." />
      <div className="flex flex-col gap-6">
        <section aria-labelledby="account-heading">
          <h2 id="account-heading" className="mb-3 text-sm font-semibold text-ink">
            Account
          </h2>
          <Card>
            <dl className="flex flex-col gap-3 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-ink-muted">Name</dt>
                <dd className="text-ink">{user?.displayName ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-ink-muted">Email</dt>
                <dd className="text-ink">{user?.email ?? "—"}</dd>
              </div>
            </dl>
          </Card>
        </section>

        <section aria-labelledby="platforms-heading">
          <h2 id="platforms-heading" className="mb-3 text-sm font-semibold text-ink">
            Connected platforms
          </h2>

          {actionError ? (
            <div className="mb-3">
              <ErrorState message={actionError} />
            </div>
          ) : null}

          {connectionQuery.isError && connection !== null ? (
            <div className="mb-3">
              <ErrorState
                message="Couldn't refresh your connection status. Showing the last status loaded."
                onRetry={() => void connectionQuery.refetch()}
              />
            </div>
          ) : null}

          {connectionQuery.isError && connection === null ? (
            <ErrorState
              message={connectionQuery.error instanceof ApiError ? connectionQuery.error.message : "Could not load your TikTok connection."}
              onRetry={() => void connectionQuery.refetch()}
            />
          ) : connection === null ? (
            <Card>
              <Spinner label="Loading connection status…" />
            </Card>
          ) : (
            <PlatformConnectionCard
              name={connection.platform}
              connected={connection.connected}
              accountName={accountName}
              accountTestId="tiktok-connected-as"
            >
              {connection.connected ? (
                <Button type="button" variant="secondary" onClick={() => void handleDisconnect()}>
                  Disconnect
                </Button>
              ) : (
                <Button type="button" variant="secondary" onClick={() => void handleConnect()} disabled={connecting}>
                  {connecting ? "Connecting…" : "Connect"}
                </Button>
              )}
            </PlatformConnectionCard>
          )}

          {/* Milestone 4.0: Instagram's real status, read-only. A Connect
              button appears only once the backend reports connect_available
              (Milestone 4.1). */}
          <div className="mt-3">
            {instagramQuery.isError && instagram === null ? (
              <ErrorState message="Could not load your Instagram connection." onRetry={() => void instagramQuery.refetch()} />
            ) : instagram === null ? (
              <Card>
                <Spinner label="Loading connection status…" />
              </Card>
            ) : (
              <PlatformConnectionCard
                name={instagram.platform}
                connected={instagram.connected}
                accountName={instagram.account_label}
                note={!instagram.connected && !instagram.connect_available ? "Connecting Instagram is coming soon." : undefined}
              />
            )}
          </div>
        </section>
      </div>
    </>
  );
}
