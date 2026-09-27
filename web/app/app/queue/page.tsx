"use client";

import { QueueBoard } from "@/components/app/QueueBoard";
import { QueueScheduling } from "@/components/app/QueueScheduling";
import { PageHeader } from "@/components/ui/PageHeader";
import { useSession } from "@/lib/session";

/**
 * /app/queue (Milestone 3.8/3.8.1/3.9). Two sections:
 *
 *   Posting rhythm (components/app/QueueScheduling.tsx) — the recurring
 *   cadence that defines future OPEN slot capacity (PUT /api/cadence).
 *
 *   Queue (components/app/QueueBoard.tsx, Milestone 3.9: Queue + Calendar
 *   Functionality) — what actually occupies that capacity: real
 *   content_slots with their assigned video and publish state
 *   (GET /api/queue/slots), a list/calendar toggle, manual assignment,
 *   automatic (FIFO) assignment for unscheduled Library videos, and
 *   "Remove from schedule."
 *
 * components/app/QueueItemCard.tsx (a Milestone 3.5 placeholder,
 * `QueueItem` type in lib/api/types.ts) was never wired up to real data
 * and is not what this milestone built on top of — the real shape needed
 * a video reference and a derived display_status QueueItem never had (see
 * QueueSlotResponse instead). It's left alone, unused, rather than deleted
 * as part of this change — a separate, explicit cleanup, not a
 * side effect of unrelated work.
 */
export default function QueuePage() {
  const { accessToken } = useSession();

  return (
    <>
      <PageHeader title="Queue" description="Your recurring posting rhythm and what's scheduled." />
      <div className="flex flex-col gap-6">
        <section aria-labelledby="posting-rhythm-heading">
          <h2 id="posting-rhythm-heading" className="mb-3 text-sm font-semibold text-ink">
            Posting rhythm
          </h2>
          <QueueScheduling accessToken={accessToken} />
        </section>

        <section aria-labelledby="queue-heading">
          <h2 id="queue-heading" className="mb-3 text-sm font-semibold text-ink">
            Queue
          </h2>
          <QueueBoard accessToken={accessToken} />
        </section>
      </div>
    </>
  );
}
