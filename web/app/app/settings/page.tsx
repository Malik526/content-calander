"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { mockPlatformConnections } from "@/lib/api/mockData";
import { useSession } from "@/lib/session";

/**
 * /app/settings — shell only (Milestone 3.5). Account section reflects
 * the real session boundary (lib/session.tsx — dev mock today, a real
 * session later, same component either way). Platform connections
 * section shows connection *status* only — TikTok OAuth itself is
 * Milestone 3.6's scope, explicitly not pulled forward (the "Connect"
 * action below is inert by design, matching Library's upload
 * placeholder).
 */
export default function SettingsPage() {
  const { user } = useSession();

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
          <div className="flex flex-col gap-3">
            {mockPlatformConnections.map((connection) => (
              <Card
                key={connection.id}
                className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium capitalize text-ink">{connection.platform}</p>
                  {connection.accountLabel ? (
                    <span className="text-xs text-ink-muted">{connection.accountLabel}</span>
                  ) : null}
                </div>
                <div className="flex items-center gap-3">
                  <Badge tone={connection.status === "connected" ? "success" : "pending"}>
                    {connection.status === "connected" ? "Connected" : "Not connected"}
                  </Badge>
                  <Button href="#" variant="secondary">
                    Connect
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
