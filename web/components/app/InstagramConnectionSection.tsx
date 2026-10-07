"use client";

/**
 * InstagramConnectionSection — the Instagram card on /app/settings
 * (status Milestone 4.0; Connect, Disconnect and callback outcomes 4.1).
 *
 * Reads the cached status (useInstagramConnection) and acts through
 * useInstagramActions, which keeps the user-scoped Instagram cache entry
 * current. Owns its own progress and error state, so Instagram and TikTok
 * actions never block or overwrite each other.
 *
 * On mount it consumes the `?instagram=<outcome>` parameter the backend
 * callback appends (lib/instagramCallback.ts), removes just that parameter
 * from the address bar, shows the matching message and refetches the
 * status. Connect navigates the page to Instagram's authorization URL
 * (the web adapter; a native client would open an auth session instead).
 *
 * States: loading, load error (with retry), not connected (Connect only
 * when the server reports connect_available, otherwise a short note),
 * connecting, connected as "@username" (or plain "Connected" when
 * Instagram didn't say), disconnecting, action/callback error.
 *
 * No props.
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { Spinner } from "@/components/ui/Spinner";
import { PlatformConnectionCard } from "@/components/app/PlatformConnectionCard";
import { useInstagramActions } from "@/hooks/useInstagramActions";
import { useInstagramConnection } from "@/hooks/useInstagramConnection";
import { ApiError } from "@/lib/api/client";
import {
  instagramCallbackNotice,
  takeInstagramCallbackOutcome,
  type InstagramCallbackNotice,
} from "@/lib/instagramCallback";

export function InstagramConnectionSection() {
  // --- State ---
  const instagramQuery = useInstagramConnection();
  const { startConnect, disconnect, refreshConnection } = useInstagramActions();
  const instagram = instagramQuery.data ?? null;
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [notice, setNotice] = useState<InstagramCallbackNotice | null>(null);

  useEffect(() => {
    // window.location only exists after mount (static export) — see the
    // matching TikTok effect in app/app/settings/page.tsx.
    const outcome = takeInstagramCallbackOutcome();
    if (outcome === null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNotice(instagramCallbackNotice(outcome));
    void refreshConnection();
    // Runs once per mount: the outcome is removed from the URL above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Handlers ---
  async function handleConnect() {
    setNotice(null);
    setConnecting(true);
    try {
      window.location.href = await startConnect();
    } catch (error) {
      setConnecting(false);
      setNotice({ tone: "error", message: error instanceof ApiError ? error.message : "Could not start connecting Instagram." });
    }
  }

  async function handleDisconnect() {
    setNotice(null);
    setDisconnecting(true);
    try {
      await disconnect();
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof ApiError ? error.message : "Could not disconnect Instagram." });
    } finally {
      setDisconnecting(false);
    }
  }

  // --- Render ---
  return (
    <div className="mt-3 flex flex-col gap-3">
      {notice?.tone === "error" ? <ErrorState message={notice.message} /> : null}
      {notice?.tone === "success" ? (
        <p role="status" className="text-sm text-ink-muted">
          {notice.message}
        </p>
      ) : null}

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
          accountName={instagram.connected ? instagram.account_label : null}
          accountTestId="instagram-connected-as"
          note={!instagram.connected && !instagram.connect_available ? "Connecting Instagram is coming soon." : undefined}
        >
          {instagram.connected ? (
            <Button type="button" variant="secondary" onClick={() => void handleDisconnect()} disabled={disconnecting}>
              {disconnecting ? "Disconnecting…" : "Disconnect"}
            </Button>
          ) : instagram.connect_available ? (
            <Button type="button" variant="secondary" onClick={() => void handleConnect()} disabled={connecting}>
              {connecting ? "Connecting…" : "Connect"}
            </Button>
          ) : null}
        </PlatformConnectionCard>
      )}
    </div>
  );
}
