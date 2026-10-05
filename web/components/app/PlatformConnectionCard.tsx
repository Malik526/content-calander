/**
 * PlatformConnectionCard — one connected-platform row in Settings: name,
 * optional "Connected as" identity, connection badge and the caller's
 * actions (Milestone 4.0, extracted unchanged from the TikTok card so
 * TikTok and Instagram render the same way).
 *
 * Props:
 *   name          platform id/name; rendered capitalized ("tiktok" → "Tiktok", as before)
 *   connected     drives the Connected / Not connected badge
 *   accountName   shown as "Connected as …" when present
 *   accountTestId data-testid for the "Connected as" line
 *   note          short secondary text, e.g. "Coming soon"
 *   children      actions (Connect / Disconnect buttons), if any
 */

import type { ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";

export function PlatformConnectionCard({
  name,
  connected,
  accountName,
  accountTestId,
  note,
  children,
}: {
  name: string;
  connected: boolean;
  accountName?: string | null;
  accountTestId?: string;
  note?: string;
  children?: ReactNode;
}) {
  // The unstyled group wrapper names the card for assistive tech ("tiktok
  // connection") so two platform cards' identical badges stay distinguishable.
  return (
    <div role="group" aria-label={`${name} connection`}>
      <Card className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-0.5">
          <p className="text-sm font-medium capitalize text-ink">{name}</p>
          {accountName ? (
            <p className="text-xs text-ink-muted" data-testid={accountTestId}>
              Connected as <span className="font-medium text-ink">{accountName}</span>
            </p>
          ) : null}
          {note ? <p className="text-xs text-ink-muted">{note}</p> : null}
        </div>
        <div className="flex items-center gap-3">
          <Badge tone={connected ? "success" : "pending"}>{connected ? "Connected" : "Not connected"}</Badge>
          {children}
        </div>
      </Card>
    </div>
  );
}
