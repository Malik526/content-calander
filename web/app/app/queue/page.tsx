"use client";

import { QueueScheduling } from "@/components/app/QueueScheduling";
import { PageHeader } from "@/components/ui/PageHeader";
import { useSession } from "@/lib/session";

/**
 * /app/queue (Milestone 3.8.1). Home for the hosted posting cadence:
 * "Posting rhythm" (the weekly editor, PUT /api/cadence) and "Upcoming
 * schedule" (a human-readable preview of GET /api/cadence/slots) — both
 * rendered by components/app/QueueScheduling.tsx, moved here from Settings
 * so cadence configuration and its resulting slots live together.
 *
 * Milestone 3.5's placeholder "Nothing scheduled yet" shell (a hardcoded
 * empty QueueItem[] list) is retired by this change — assigned videos and
 * published history are real 3.9 scope (a real assignment endpoint and
 * queue/calendar editing UI don't exist yet), not something to fake here.
 * components/app/QueueItemCard.tsx stays in the tree, unused by this page
 * for now, for 3.9 to pick back up.
 */
export default function QueuePage() {
  const { accessToken } = useSession();

  return (
    <>
      <PageHeader title="Queue" description="Your recurring posting rhythm and what's coming up." />
      <QueueScheduling accessToken={accessToken} />
    </>
  );
}
