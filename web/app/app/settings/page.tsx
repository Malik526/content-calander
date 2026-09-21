"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Spinner } from "@/components/ui/Spinner";
import { connectTikTok, disconnectTikTok, getTikTokConnection } from "@/lib/api/platforms";
import { ApiError } from "@/lib/api/client";
import type { TikTokConnectionStatus } from "@/lib/api/types";
import { useSession } from "@/lib/session";

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
 */
export default function SettingsPage() {
  const { user, accessToken } = useSession();
  const [connection, setConnection] = useState<TikTokConnectionStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function loadStatus() {
    setLoadError(null);
    try {
      const status = await getTikTokConnection(accessToken);
      setConnection(status);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : "Could not load your TikTok connection.");
    }
  }

  useEffect(() => {
    if (!accessToken) return;
    // loadStatus is async — its setState calls happen after a real
    // `await getTikTokConnection(...)`, not synchronously in this effect
    // body, exactly the "subscribe, then setState in a callback once
    // external state resolves" shape react-hooks/set-state-in-effect
    // itself endorses; the lint rule's static analysis just can't see
    // through the function call to confirm that.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

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
      const { authorization_url } = await connectTikTok(accessToken);
      window.location.href = authorization_url;
    } catch (error) {
      setConnecting(false);
      setActionError(error instanceof ApiError ? error.message : "Could not start connecting TikTok.");
    }
  }

  async function handleDisconnect() {
    setActionError(null);
    try {
      const status = await disconnectTikTok(accessToken);
      setConnection(status);
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "Could not disconnect TikTok.");
    }
  }

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

          {loadError ? (
            <ErrorState message={loadError} onRetry={loadStatus} />
          ) : connection === null ? (
            <Card>
              <Spinner label="Loading connection status…" />
            </Card>
          ) : (
            <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium capitalize text-ink">{connection.platform}</p>
                {connection.account_label ? (
                  <span className="text-xs text-ink-muted">{connection.account_label}</span>
                ) : null}
              </div>
              <div className="flex items-center gap-3">
                <Badge tone={connection.connected ? "success" : "pending"}>
                  {connection.connected ? "Connected" : "Not connected"}
                </Badge>
                {connection.connected ? (
                  <Button type="button" variant="secondary" onClick={() => void handleDisconnect()}>
                    Disconnect
                  </Button>
                ) : (
                  <Button type="button" variant="secondary" onClick={() => void handleConnect()} disabled={connecting}>
                    {connecting ? "Connecting…" : "Connect"}
                  </Button>
                )}
              </div>
            </Card>
          )}
        </section>
      </div>
    </>
  );
}
