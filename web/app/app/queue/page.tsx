import { QueueItemCard } from "@/components/app/QueueItemCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { QueueItem } from "@/lib/api/types";

export const metadata = { title: "Queue" };

// Real shipped behavior: nothing scheduled yet (no backend call exists —
// see lib/api/client.ts). QueueItemCard's populated layout is still real
// and tested (components/app/QueueItemCard.tsx,
// tests/components/QueueItemCard.test.tsx) — this page renders it the
// moment `items` is non-empty, without any other change.
const items: QueueItem[] = [];

/**
 * /app/queue — shell only (Milestone 3.5). Scheduling/cadence controls
 * are Milestone 3.8's scope — this page only displays, never edits.
 */
export default function QueuePage() {
  return (
    <>
      <PageHeader title="Queue" description="What's scheduled to publish and what already has." />
      {items.length === 0 ? (
        <EmptyState
          title="Nothing scheduled yet"
          description="Once you have videos in your library, scheduled and published posts will show up here."
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((item) => (
            <li key={item.id}>
              <QueueItemCard item={item} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
